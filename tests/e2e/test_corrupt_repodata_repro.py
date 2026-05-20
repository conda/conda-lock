"""End-to-end reproduction of conda/conda-lock#896 from PR #862.

The harness committed in ``tests/test-corrupt-repodata/`` captures
the *exact* failure: it pins the metadata that micromamba 2.1.1
wrote to the cache when installing from an explicit lockfile, plus
the 2.1.0 baseline for comparison. Driving conda-lock against that
corrupt metadata is the only way to exercise the full chain
(LINK-only dryrun -> ``reconstruct_fetch_actions`` ->
``get_repodata_record`` -> cache-side heal ->
``apply_categories`` forward walk -> ``assert_no_orphaned_conda_packages``
-> v1 serialization). A unit-style test that hand-crafts a corrupt
``LockedDependency`` cannot reach the silent-vanishing path because
it bypasses ``apply_categories`` and ``to_v1()`` entirely.

The supporting fixtures (cache warming, corrupt metadata overlay,
``conda-lock lock`` driver, explicit-render diff) live in
``tests.support.corrupt_repodata`` so future component-level tests
can reuse them.
"""

# mypy: disable-error-code="arg-type,comparison-overlap"

from __future__ import annotations

import os
import subprocess
import sys

from pathlib import Path

import pytest

from conda_lock.invoke_conda import _ensureconda
from tests.support.corrupt_repodata import (
    REPRO_DIR,
    conda_lock_lock_against,
    conda_lock_render_explicit,
    explicit_lockfile_urls,
    overlay_corrupt_metadata,
    parse_lockfile_packages,
)


@pytest.fixture(scope="module")
def reproduces_corrupt_2_1_1_cache(tmp_path_factory):
    """Reconstruct stage 04 of PR #862 *without Docker*:

    - warm a fresh package cache by ``micromamba create`` from the
      committed ``01-explicit.lock`` (so all package tarballs land in
      the cache with whatever metadata the *current* solver writes,
      i.e. correct on mamba 2.6.0+);
    - extract the committed ``2.1.1-pkgs.tar.gz`` (only metadata --
      info/index.json + info/repodata_record.json, the latter exactly as
      mamba 2.1.1 wrote it);
    - copy the corrupt metadata over the warmed cache.

    The result is a "hybrid" cache: real package files + bug-faithful
    corrupt metadata. Reused across tests in this module via module
    scope -- it takes ~30 seconds to warm.
    """
    micromamba = _ensureconda(
        mamba=False, micromamba=True, conda=False, conda_exe=False
    )
    if micromamba is None:
        pytest.skip("micromamba not installed -- needed to warm cache")
    explicit_lock = REPRO_DIR / "01-explicit.lock"
    corrupt_archive = REPRO_DIR / "2.1.1-pkgs.tar.gz"
    if not explicit_lock.is_file() or not corrupt_archive.is_file():
        pytest.skip("PR #862 fixtures not present in tests/test-corrupt-repodata/")

    work = tmp_path_factory.mktemp("repro-2.1.1")
    cache = work / "cache"
    prefix = work / "prefix"
    cache.mkdir()
    proc = subprocess.run(
        [
            str(micromamba),
            "create",
            "--prefix",
            str(prefix),
            "--yes",
            "--override-channels",
            "--channel",
            "conda-forge",
            "--file",
            str(explicit_lock),
        ],
        env={
            **os.environ,
            "CONDA_PKGS_DIRS": str(cache),
            "CONDA_SUBDIR": "linux-64",
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        pytest.skip(f"could not warm cache via {micromamba}: {proc.stderr[-2000:]}")

    extracted = work / "2.1.1-pkgs"
    subprocess.run(
        ["tar", "-xzf", str(corrupt_archive), "-C", str(work)],
        check=True,
    )
    overlaid = overlay_corrupt_metadata(extracted, cache)
    if overlaid == 0:
        pytest.skip(
            "no packages from 2.1.1-pkgs found in warmed cache; "
            "fixture and explicit lock have drifted apart"
        )

    return cache


@pytest.fixture(scope="module")
def conda_solver_path():
    """Locate ``conda-standalone`` (a single-file conda binary -- the
    one ``conda-lock --conda=...`` accepts when you don't have full
    conda installed). Stage 04 of PR #862 explicitly tests this solver
    in addition to mamba; conda-standalone reads the same leaked
    ``pkgs_dirs`` and is therefore subject to the same #896 chain."""
    candidates = [
        Path(os.environ.get("CONDA", "") or "/opt/conda/standalone_conda/conda.exe"),
        Path(sys.prefix) / "standalone_conda" / "conda.exe",
        Path(sys.prefix) / "bin" / "conda",
    ]
    if "MAMBA_ROOT_PREFIX" in os.environ:
        candidates.append(
            Path(os.environ["MAMBA_ROOT_PREFIX"])
            / "envs"
            / "conda-lock-dev"
            / "standalone_conda"
            / "conda.exe"
        )
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    pytest.skip(
        "conda-standalone not installed; install via "
        "`micromamba install -c conda-forge conda-standalone`"
    )


@pytest.fixture(scope="module")
def mamba_solver_path():
    """Locate ``mamba`` for the e2e -- separate fixture so the test can
    parametrize over solver choice and skip cleanly when one is
    absent."""
    mamba = _ensureconda(mamba=True, micromamba=False, conda=False, conda_exe=False)
    if mamba is None:
        pytest.skip("mamba not installed -- needed to drive conda-lock end-to-end")
    return mamba


@pytest.mark.corrupt_cache_repro
@pytest.mark.timeout(900)
@pytest.mark.parametrize("solver", ["mamba", "conda-standalone"])
def test_pr862_corrupt_2_1_1_cache_does_not_drop_packages(
    reproduces_corrupt_2_1_1_cache,
    request,
    tmp_path,
    solver,
):
    """PR #862 stage 04 + 05 against the **2.1.1 corrupt cache**.

    Drives the *full* committed reproduction pipeline minus the Docker
    wrapper:

    - Stage 04: ``conda-lock lock --conda=<solver>`` against the
      hybrid (real-files + corrupt-metadata) cache.
    - Stage 05: ``conda-lock render --kind=explicit`` to the explicit
      lockfile that ``conda-lock install`` consumes verbatim.

    Both stages run in two parallel scenarios -- against the corrupt
    cache and against a fresh clean cache -- and the test asserts:

    1. *Structural lockfile equivalence:* parsing both unified lockfiles
       via ``parse_conda_lock_file`` produces the same set of
       ``(name, version, category)`` tuples. This catches both silent
       drops and any incorrect rescue-into-main implementation (which
       would manifest as the same name appearing in different
       categories between corrupt and clean runs).
    2. *Explicit-URL equivalence:* the rendered explicit lockfiles --
       what ``conda-lock install`` actually consumes -- have identical
       URL sets. PR #862 stage 05's whole point is that "packages are
       missing from the resulting environment if and only if they are
       missing from the explicit lockfile", so this is the user-visible
       surface.

    Parametrized over both ``mamba`` and ``conda-standalone`` because
    stage 04 itself runs both: different conda-flavoured binaries
    handle corrupt metadata and leaked ``pkgs_dirs`` slightly
    differently, and the bug surfaces in both.
    """
    if solver == "mamba":
        conda_exe = request.getfixturevalue("mamba_solver_path")
    elif solver == "conda-standalone":
        conda_exe = request.getfixturevalue("conda_solver_path")
    else:
        pytest.fail(f"unknown solver: {solver}")

    dev_env = REPRO_DIR.parent.parent / "environments/dev-environment.yaml"
    if not dev_env.is_file():
        pytest.skip("dev-environment.yaml not present at repo root")

    work = tmp_path
    out_corrupt = work / "unified-corrupt.yml"
    out_clean = work / "unified-clean.yml"

    # Stage 04: lock against the corrupt cache.
    proc = conda_lock_lock_against(
        reproduces_corrupt_2_1_1_cache,
        conda_exe=str(conda_exe),
        source=dev_env,
        out_lockfile=out_corrupt,
    )
    assert proc.returncode == 0, (
        f"conda-lock against corrupt cache (solver={solver}) failed:\n"
        f"STDOUT:\n{proc.stdout[-2000:]}\n"
        f"STDERR:\n{proc.stderr[-2000:]}"
    )

    # Live baseline: lock against an empty cache so package versions
    # match. The committed 2.1.0 fixture is months old and would drift
    # against current conda-forge state.
    clean_cache = work / "clean-cache"
    clean_cache.mkdir()
    proc_clean = conda_lock_lock_against(
        clean_cache,
        conda_exe=str(conda_exe),
        source=dev_env,
        out_lockfile=out_clean,
    )
    assert proc_clean.returncode == 0, (
        f"conda-lock against clean cache (solver={solver}) failed:\n"
        f"STDOUT:\n{proc_clean.stdout[-2000:]}\n"
        f"STDERR:\n{proc_clean.stderr[-2000:]}"
    )

    # 1. Structural equivalence via the production parser.
    corrupt_pkgs = parse_lockfile_packages(out_corrupt)
    clean_pkgs = parse_lockfile_packages(out_clean)

    def _key(p):
        # ``LockedDependency`` (v2 model): includes categories so a
        # category-mutation bug shows up here even if names match.
        return (p.manager, p.name, p.version, frozenset(p.categories))

    corrupt_keys = {_key(p) for p in corrupt_pkgs}
    clean_keys = {_key(p) for p in clean_pkgs}

    # The committed corrupt-reference lockfile from PR #862 (unfixed)
    # has 110 packages -- a useful sanity floor.
    pr862_corrupt_ref = REPRO_DIR / f"lockfile-2.1.1-pkgs-lock-with-{solver}.yml"
    pr862_corrupt_ref_count = (
        len(parse_lockfile_packages(pr862_corrupt_ref))
        if pr862_corrupt_ref.is_file()
        else 0
    )

    assert corrupt_keys == clean_keys, (
        "structural mismatch between corrupt-cache and clean-cache "
        "lockfiles -- the cache-corruption mitigations did not produce "
        "an equivalent lockfile.\n"
        f"  solver:                  {solver}\n"
        f"  corrupt-cache packages:  {len(corrupt_pkgs)}\n"
        f"  clean-cache packages:    {len(clean_pkgs)}\n"
        f"  PR #862 unfixed ref:     {pr862_corrupt_ref_count}\n"
        f"  only in clean:   {sorted(clean_keys - corrupt_keys)[:8]} ...\n"
        f"  only in corrupt: {sorted(corrupt_keys - clean_keys)[:8]} ..."
    )

    # 2. Stage 05: render explicit lockfiles and compare URL sets.
    explicit_corrupt = conda_lock_render_explicit(
        out_corrupt, work / "explicit-from-corrupt"
    )
    explicit_clean = conda_lock_render_explicit(out_clean, work / "explicit-from-clean")

    corrupt_urls = explicit_lockfile_urls(explicit_corrupt)
    clean_urls = explicit_lockfile_urls(explicit_clean)
    assert corrupt_urls == clean_urls, (
        "explicit-render URL set differs between corrupt and clean cache "
        "-- this is the exact surface a `conda-lock install` consumes, "
        "so any difference here is a user-visible installation drift.\n"
        f"  solver: {solver}\n"
        f"  only in clean:   {sorted(clean_urls - corrupt_urls)[:8]}\n"
        f"  only in corrupt: {sorted(corrupt_urls - clean_urls)[:8]}"
    )

    # 3. Empty-deps sanity. Without the fix, the committed reference
    # has ~78 entries with empty dependencies for the mamba run; with
    # the fix we should be in the same ballpark as the clean control.
    corrupt_empty = sum(1 for p in corrupt_pkgs if not p.dependencies)
    clean_empty = sum(1 for p in clean_pkgs if not p.dependencies)
    assert corrupt_empty <= clean_empty + 2, (
        f"corrupt-cache lockfile has {corrupt_empty} entries with empty "
        f"dependencies, well above the legitimate baseline of "
        f"{clean_empty} -- the healing pipeline did not recover all "
        f"corrupt depends. (solver={solver})"
    )
