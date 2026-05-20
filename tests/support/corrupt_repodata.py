"""Shared fixtures and helpers for the PR #862 corrupt-cache repro.

The tests in ``tests/e2e/test_corrupt_repodata_repro.py`` reconstruct
stage 04 of the PR #862 pipeline without Docker: warm a fresh
package cache via ``micromamba create``, then overlay the corrupt
metadata bundled in ``tests/test-corrupt-repodata/2.1.1-pkgs.tar.gz``.
The helpers here own that dance plus the stage-05 explicit-render
diff so individual tests can stay focused on assertions.

Kept under ``tests/support/`` rather than inline in the e2e file so
new component-level tests in the future can reuse the warmed cache
without copy-paste.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent.parent
REPRO_DIR = TESTS_DIR / "test-corrupt-repodata"


def overlay_corrupt_metadata(corrupt_pkgs_root: Path, cache: Path) -> int:
    """Mirror stage 04 of the PR #862 pipeline: copy the corrupt
    ``info/{index,repodata_record}.json`` from the extracted reference
    archive over the warmed cache. After this the cache has real package
    files (downloaded by ``micromamba create``) but the metadata exactly
    matches what micromamba 2.1.1 would have written.
    """
    overlaid = 0
    for pkg_dir in corrupt_pkgs_root.iterdir():
        if not pkg_dir.is_dir():
            continue
        src_info = pkg_dir / "info"
        if not src_info.is_dir():
            continue
        for tgt_info in cache.glob(f"**/{pkg_dir.name}/info"):
            for f in src_info.iterdir():
                shutil.copy2(f, tgt_info / f.name)
            overlaid += 1
    return overlaid


def conda_lock_lock_against(
    cache: Path,
    *,
    conda_exe: str,
    source: Path,
    out_lockfile: Path,
) -> subprocess.CompletedProcess:
    """Drive ``conda-lock lock`` with ``CONDA_PKGS_DIRS=<cache>`` --
    exactly the recipe from ``04-run-conda-lock.sh`` minus the Docker
    wrapper."""
    return subprocess.run(
        [
            "conda-lock",
            "lock",
            "--micromamba",
            f"--file={source}",
            "--platform=linux-64",
            f"--conda={conda_exe}",
            f"--lockfile={out_lockfile}",
        ],
        env={**os.environ, "CONDA_PKGS_DIRS": str(cache)},
        capture_output=True,
        text=True,
        timeout=600,
    )


def conda_lock_render_explicit(lockfile: Path, out: Path) -> Path:
    """``conda-lock render --kind=explicit`` of an existing unified
    lockfile -- exactly what stage 05 (``05-render-explicit-lockfiles.py``)
    does. The explicit lockfile is what ``conda-lock install`` consumes
    just before installation, so its URL set is the *truest* statement
    of which packages will land in the user's environment. Comparing
    explicit URLs catches category-mutation bugs that a raw YAML name
    set would miss (a v1 lockfile entry only renders to the explicit
    output when its category survives ``--filter-categories``).

    Returns the path to the rendered explicit lockfile.
    """
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / "explicit.lock"
    proc = subprocess.run(
        [
            "conda-lock",
            "render",
            "--kind=explicit",
            "--platform=linux-64",
            f"--filename-template={out_file}",
            str(lockfile),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"conda-lock render failed:\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\n"
            f"STDERR:\n{proc.stderr[-2000:]}"
        )
    if not out_file.is_file():
        raise RuntimeError(
            f"conda-lock render did not produce expected file at {out_file}; "
            f"see {out.parent} for the actual output."
        )
    return out_file


def explicit_lockfile_urls(explicit_lock: Path) -> set[str]:
    """Pull the package-URL set out of an explicit lockfile.

    The format is one URL (with optional ``#md5`` suffix) per non-comment
    line, after the ``@EXPLICIT`` marker.
    """
    urls: set[str] = set()
    seen_marker = False
    for line in explicit_lock.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "@EXPLICIT":
            seen_marker = True
            continue
        if not seen_marker:
            continue
        urls.add(line.split("#", 1)[0])
    return urls


def parse_lockfile_packages(lockfile: Path):
    """Parse a unified conda-lock lockfile into ``LockedDependency``
    objects via the production parser, *not* line-grepping. Surfaces
    category mutation bugs that a YAML-name comparison would miss."""
    from conda_lock.lockfile import parse_conda_lock_file

    parsed = parse_conda_lock_file(lockfile)
    return parsed.package
