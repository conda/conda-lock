"""Component tests for ``conda_lock.solver.lockfile_heal``.

Carry-forward repair: when the previous lockfile was generated
against a corrupt mamba 2.1.1-2.3.3 cache, the entries it carries
have empty ``dependencies``. ``heal_locked_dependencies_from_cache``
walks the local cache for ``info/index.json`` and returns a
``LockfileHealReport`` so callers can decide whether to warn,
continue, or escalate.

The two ``test_update_specs_for_arch_*`` tests are component tests
because they drive the full ``update_specs_for_arch`` orchestration
path -- including the now-explicit handling of the ``healed`` and
``ambiguous`` tuples -- against a fake ``get_pkgs_dirs`` and a
fake ``_get_installed_conda_packages``.
"""

# mypy: disable-error-code="arg-type,comparison-overlap"

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from conda_lock.conda_solver import update_specs_for_arch
from conda_lock.lockfile.v1.models import HashModel
from conda_lock.lockfile.v2prelim.models import LockedDependency
from conda_lock.models.channel import Channel
from conda_lock.solver.lockfile_heal import (
    LockfileHealReport,
    heal_locked_dependencies_from_cache,
    locked_dep_as_link_action,
)
from tests.support.fixtures import MAMBA_26_LINK_ACTION as _MAMBA_26_LINK_ACTION


def _corrupt_lockfile_entry(
    name: str = "libzlib",
    version: str = "1.3.2",
    fn: str = "libzlib-1.3.2-h25fd6f3_2.conda",
    md5: str = "d87ff7921124eccd67248aa483c23fec",
    sha256: str = "55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
) -> LockedDependency:
    """Build a ``LockedDependency`` with empty ``dependencies`` -- the
    shape produced by conda-lock running against a corrupt mamba
    2.1.1-2.3.2 cache."""
    return LockedDependency(
        name=name,
        version=version,
        manager="conda",
        platform="linux-64",
        # The bug's signature: empty dependencies on a package that
        # legitimately has at least one (libzlib needs __glibc).
        dependencies={},
        url=("https://conda.anaconda.org/conda-forge/linux-64/" + fn),
        hash=HashModel(md5=md5, sha256=sha256),
        categories={"main"},
    )


def test_locked_dep_as_link_action_extracts_dist_name(tmp_path: Path):
    """Sanity-check the projection so the cache-lookup helpers below
    can find the matching ``info/index.json``."""
    dep = _corrupt_lockfile_entry()
    link_action, dist_name = locked_dep_as_link_action(dep)
    assert dist_name == "libzlib-1.3.2-h25fd6f3_2"
    assert link_action["url"] == dep.url
    assert link_action["fn"] == "libzlib-1.3.2-h25fd6f3_2.conda"
    assert link_action["platform"] == "linux-64"


def _populate_cache_info_index(
    pkgs_dir: Path, dist_name: str, *, depends: list[str], **extra: Any
) -> None:
    """Lay out ``<pkgs_dir>/<scheme>/<host>/<channel>/<platform>/<dist>/info/{index,repodata_record}.json``
    matching the mamba 2.6.0 hierarchical layout."""
    info_dir = (
        pkgs_dir / "https/conda.anaconda.org/conda-forge/linux-64" / dist_name / "info"
    )
    info_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "name": "libzlib",
        "version": "1.3.2",
        "build": "h25fd6f3_2",
        "build_number": 2,
        "depends": depends,
        "license": "Zlib",
        "subdir": "linux-64",
        "timestamp": 1774072609,
        **extra,
    }
    (info_dir / "index.json").write_text(json.dumps(index))


def test_heal_locked_dependencies_from_cache_recovers_empty_deps(
    tmp_path: Path, monkeypatch
):
    """End-to-end: a corrupt LockedDependency with empty ``dependencies``
    is healed from the package cache. We populate only ``info/index.json``
    (no ``repodata_record.json``), so the heal exercises the corruption
    path that recovers from the index file."""
    pkgs_dir = tmp_path / "pkgs"
    _populate_cache_info_index(
        pkgs_dir,
        "libzlib-1.3.2-h25fd6f3_2",
        depends=["__glibc >=2.17,<3.0.a0"],
    )
    # repodata_record.json itself can be the buggy mamba-2.1.1-2.3.2
    # shape -- the heal pipeline notices and falls through to
    # info/index.json. We populate it that way to exercise the chain.
    info_dir = (
        pkgs_dir
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
    )
    (info_dir / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "depends": [],
                "license": "",
                "timestamp": 0,
                "url": _MAMBA_26_LINK_ACTION["url"],
                "md5": _MAMBA_26_LINK_ACTION["md5"],
                "sha256": _MAMBA_26_LINK_ACTION["sha256"],
                "subdir": "linux-64",
            }
        )
    )

    locked = {"libzlib": _corrupt_lockfile_entry()}
    assert locked["libzlib"].dependencies == {}

    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs_dir],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert len(report.healed) == 1
    assert locked["libzlib"].dependencies == {"__glibc": ">=2.17,<3.0.a0"}


def test_heal_locked_dependencies_from_cache_returns_zero_when_clean(
    tmp_path, monkeypatch
):
    """No empty-deps entries -> zero work, ``get_pkgs_dirs`` not even called."""
    locked = {
        "libzlib": LockedDependency(
            name="libzlib",
            version="1.3.2",
            manager="conda",
            platform="linux-64",
            dependencies={"__glibc": ">=2.17"},
            url="https://conda.example.com/libzlib.conda",
            hash=HashModel(md5="a" * 32, sha256="b" * 64),
            categories={"main"},
        )
    }

    def boom(**_kwargs):
        raise AssertionError("get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.solver.lockfile_heal.get_pkgs_dirs", boom)
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert len(report.healed) == 0
    assert locked["libzlib"].dependencies == {"__glibc": ">=2.17"}


def test_heal_locked_dependencies_reports_ambiguous_when_all_cache_missing(
    tmp_path, monkeypatch
):
    """*All* suspect entries unreachable in the cache: cache is just
    empty (e.g. fresh CI environment, user has not yet run
    ``conda-lock install``). The heal layer cannot tell legit-empty
    (``tzdata``, ``python_abi``) from corrupt-empty here, so it
    surfaces the entry under ``ambiguous`` and leaves the lockfile
    entry untouched. Logging the operator-facing WARNING is the
    orchestration layer's job (``update_specs_for_arch``); see
    ``test_update_specs_for_arch_warns_on_ambiguous`` below.
    """
    locked = {"libzlib": _corrupt_lockfile_entry()}
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "empty-pkgs"],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert isinstance(report, LockfileHealReport)
    assert report.healed == ()
    assert report.confirmed_legit_empty == ()
    assert report.ambiguous == ("libzlib",)
    # The lockfile entry is left untouched: heal is read-only when
    # there is no cache evidence either way.
    assert locked["libzlib"].dependencies == {}


def test_heal_locked_dependencies_heals_what_it_can_and_reports_the_rest(
    tmp_path, monkeypatch
):
    """Healed-elsewhere is *not* per-entry evidence about ambiguous
    entries. A user's cache might happen to contain the
    ``info/index.json`` for one corrupt-cache-derived empty-deps entry
    (so we heal it in place) AND not yet contain ``info/index.json``
    for a legit-empty leaf (``tzdata`` etc.). The legit leaf must NOT
    be flagged as suspicious just because the other entry was healed.

    The heal layer's contract is to classify each entry into
    ``healed`` (corrupt-with-evidence), ``confirmed_legit_empty``
    (legit-with-evidence), or ``ambiguous`` (no evidence). Logging
    is the orchestration layer's job.
    """
    pkgs = tmp_path / "pkgs"
    # libzlib: lockfile says empty deps, cache says non-empty -> healed.
    _populate_cache_info_index(
        pkgs,
        "libzlib-1.3.2-h25fd6f3_2",
        depends=["__glibc >=2.17,<3.0.a0"],
    )
    locked = {
        # Healable: cache contradicts the lockfile.
        "libzlib": _corrupt_lockfile_entry(
            name="libzlib", fn="libzlib-1.3.2-h25fd6f3_2.conda"
        ),
        # Ambiguous: legit-empty leaf that just happens to not be in
        # the partial cache. Must NOT be re-classified as healed.
        "tzdata": _corrupt_lockfile_entry(name="tzdata", fn="tzdata-2025c-bld.conda"),
    }
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    # Healed in place: libzlib's deps recovered.
    assert report.healed == ("libzlib",)
    assert locked["libzlib"].dependencies == {"__glibc": ">=2.17,<3.0.a0"}
    # tzdata stayed ambiguous, untouched.
    assert report.ambiguous == ("tzdata",)
    assert locked["tzdata"].dependencies == {}
    assert report.confirmed_legit_empty == ()


def test_heal_locked_dependencies_classifies_partial_cache_correctly(
    tmp_path, monkeypatch
):
    """The cache happens to contain ``info/index.json`` for *one*
    legit-empty package (``tzdata``, ``_libgcc_mutex``, ...) but not
    others. Partial caches are normal and provide no evidence that
    the other empty-deps entries are corrupt. The heal layer's
    classification reflects that:
    ``confirmed_legit_empty`` for the entry the cache agreed about,
    ``ambiguous`` for the rest. Nothing is healed.

    The architectural payoff: an earlier predicate that combined
    ``healed > 0`` with ``confirmed_legit_empty > 0`` to hard-fail
    is now structurally impossible -- the report exposes the three
    categories as separate tuples, so the orchestration layer can't
    accidentally collapse them.
    """
    pkgs = tmp_path / "pkgs"
    # tzdata is canonically empty-deps; the cache confirms.
    _populate_cache_info_index(
        pkgs,
        "tzdata-2025c-bld",
        depends=[],
        name="tzdata",
        version="2025c",
        build="bld",
    )
    locked = {
        "tzdata": _corrupt_lockfile_entry(
            name="tzdata", version="2025c", fn="tzdata-2025c-bld.conda"
        ),
        "the_other": _corrupt_lockfile_entry(
            name="the_other", fn="the_other-1.0-bld.conda"
        ),
    }
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert report.healed == ()
    assert report.confirmed_legit_empty == ("tzdata",)
    assert report.ambiguous == ("the_other",)
    # Both entries are left untouched.
    assert locked["tzdata"].dependencies == {}
    assert locked["the_other"].dependencies == {}


def test_heal_locked_dependencies_accepts_legitimate_empty_deps(tmp_path, monkeypatch):
    """``info/index.json`` is the canonical source of truth: when it
    says a package legitimately has no dependencies (e.g.
    ``nlohmann_json-abi``), trust it and leave the lockfile entry as-is
    rather than treating the situation as un-healable."""
    pkgs_dir = tmp_path / "pkgs"
    info_dir = (
        pkgs_dir
        / "https/conda.anaconda.org/conda-forge/noarch"
        / "noop-1.0.0-bld"
        / "info"
    )
    info_dir.mkdir(parents=True)
    (info_dir / "index.json").write_text(
        json.dumps(
            {
                "name": "noop",
                "version": "1.0.0",
                "build": "bld",
                "depends": [],  # canonically empty
                "subdir": "noarch",
                "license": "MIT",
                "timestamp": 1,
            }
        )
    )
    locked = {
        "noop": LockedDependency(
            name="noop",
            version="1.0.0",
            manager="conda",
            platform="linux-64",
            dependencies={},
            url=("https://conda.anaconda.org/conda-forge/noarch/noop-1.0.0-bld.conda"),
            hash=HashModel(md5="a" * 32, sha256="b" * 64),
            categories={"main"},
        )
    }
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs_dir],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert len(report.healed) == 0  # nothing to heal -- entry was always correct
    assert locked["noop"].dependencies == {}


def test_heal_locked_dependencies_rejects_legacy_flat_cross_channel_index(
    tmp_path, monkeypatch
):
    """The legacy flat cache layout (``<pkgs>/<dist>/info/...``) is
    keyed on ``dist_name`` alone and can hold same-name+version+build
    packages from different channels. ``info/index.json`` doesn't
    carry the URL or channel, so an unchecked match would silently
    heal the lockfile with metadata from the wrong artifact (the
    reviewer's #896 cross-channel-collision case for the heal path).

    Setup: corrupt lockfile entry for ``libzlib`` from conda-forge.
    Hierarchical (URL-derived) cache path is empty. Legacy flat path
    is populated with ``info/index.json`` from a *different* artifact
    that happens to share the same ``dist_name`` -- a Python 3.13
    build instead of 3.14, with a different ``build`` string. The
    heal must reject the cross-package match.
    """
    pkgs = tmp_path / "pkgs"
    impostor_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    impostor_info.mkdir(parents=True)
    (impostor_info / "index.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                # Different build string from the LINK's
                # ``libzlib-1.3.2-h25fd6f3_2.conda``: the LINK parses
                # ``h25fd6f3_2`` from the filename, but this index
                # claims it was built differently.
                "build": "DIFFERENT_BUILD",
                "depends": ["impostor-dep"],
                "subdir": "linux-64",
            }
        )
    )
    locked = {
        "libzlib": _corrupt_lockfile_entry(
            name="libzlib", fn="libzlib-1.3.2-h25fd6f3_2.conda"
        )
    }
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    # Reject -> nothing healed; the lockfile entry stays empty rather
    # than being poisoned with ``impostor-dep``.
    assert len(report.healed) == 0
    assert locked["libzlib"].dependencies == {}


def test_heal_locked_dependencies_rejects_legacy_flat_subdir_mismatch(
    tmp_path, monkeypatch
):
    """Same shape as the cross-channel test, but the impostor differs
    by ``subdir``. A linux-64 LINK must not heal from an osx-arm64
    ``info/index.json`` even when ``dist_name`` happens to match.
    Noarch is the only allowed cross-subdir match.
    """
    pkgs = tmp_path / "pkgs"
    impostor_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    impostor_info.mkdir(parents=True)
    (impostor_info / "index.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "build": "h25fd6f3_2",
                "depends": ["impostor-dep"],
                # Different concrete subdir.
                "subdir": "osx-arm64",
            }
        )
    )
    locked = {
        "libzlib": _corrupt_lockfile_entry(
            name="libzlib", fn="libzlib-1.3.2-h25fd6f3_2.conda"
        )
    }
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert len(report.healed) == 0
    assert locked["libzlib"].dependencies == {}


def test_heal_locked_dependencies_accepts_noarch_index_for_concrete_subdir(
    tmp_path, monkeypatch
):
    """Conversely, a noarch ``info/index.json`` should heal a
    concrete-platform LINK (and vice versa). Noarch packages are
    legitimately deployable across platforms, so subdir compat allows
    the cross-match."""
    pkgs = tmp_path / "pkgs"
    info_dir = pkgs / "noop-1.0.0-bld" / "info"
    info_dir.mkdir(parents=True)
    (info_dir / "index.json").write_text(
        json.dumps(
            {
                "name": "noop",
                "version": "1.0.0",
                "build": "bld",
                "depends": ["python"],
                "subdir": "noarch",
            }
        )
    )
    locked = {
        "noop": LockedDependency(
            name="noop",
            version="1.0.0",
            manager="conda",
            platform="linux-64",  # concrete subdir
            dependencies={},
            url="https://conda.anaconda.org/conda-forge/noarch/noop-1.0.0-bld.conda",
            hash=HashModel(md5="a" * 32, sha256="b" * 64),
            categories={"main"},
        )
    }
    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs],
    )
    report = heal_locked_dependencies_from_cache(
        locked, conda="/dummy", platform="linux-64"
    )
    assert len(report.healed) == 1
    assert locked["noop"].dependencies == {"python": ""}


def test_update_specs_for_arch_heals_corrupt_lockfile_carry_forward(
    tmp_path: Path, monkeypatch, caplog
):
    """The carry-forward channel: a corrupt input lockfile would
    propagate empty ``dependencies`` into the new lockfile via
    ``update_specs_for_arch`` -> ``to_fetch_action()`` -> ``solve_conda``.
    Healing must run *before* ``to_fetch_action()`` so the FETCH action
    actually carries the recovered ``depends``.
    """
    pkgs_dir = tmp_path / "pkgs"
    _populate_cache_info_index(
        pkgs_dir,
        "libzlib-1.3.2-h25fd6f3_2",
        depends=["__glibc >=2.17,<3.0.a0"],
    )

    locked = {"libzlib": _corrupt_lockfile_entry()}

    monkeypatch.setattr(
        "conda_lock.solver.lockfile_heal.get_pkgs_dirs",
        lambda **_kwargs: [pkgs_dir],
    )
    # No update target requested -> `to_update` is empty -> the function
    # short-circuits to {LINK: [], FETCH: []} for the dryrun. Heal still
    # runs first, then the unchanged-package carry-forward emits a
    # FETCH from the (now-healed) lockfile entry.
    monkeypatch.setattr(
        "conda_lock.conda_solver._get_installed_conda_packages",
        lambda *_a, **_kw: {
            "libzlib": {
                "name": "libzlib",
                "version": "1.3.2",
                "channel": "conda-forge",
                "dist_name": "libzlib-1.3.2-h25fd6f3_2",
                "platform": "linux-64",
                "base_url": "https://conda.anaconda.org/conda-forge",
            }
        },
    )

    with caplog.at_level("WARNING", logger="conda_lock.conda_solver"):
        result = update_specs_for_arch(
            conda="/dummy",
            specs=[],
            locked=locked,
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
        )
    # The FETCH action emitted for the unchanged package now carries
    # the recovered ``depends`` from the cache, not the empty list it
    # would have had without healing.
    fetched = result["actions"]["FETCH"]
    assert any(
        f["name"] == "libzlib" and f.get("depends") == ["__glibc >=2.17,<3.0.a0"]
        for f in fetched
    )
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "Healed 1 lockfile entry" in msgs
    assert "896" in msgs


def test_update_specs_for_arch_does_not_warn_when_lockfile_clean(
    tmp_path, monkeypatch, caplog
):
    """Negative control: a healthy input lockfile triggers no heal warning."""
    healthy = LockedDependency(
        name="libzlib",
        version="1.3.2",
        manager="conda",
        platform="linux-64",
        dependencies={"__glibc": ">=2.17,<3.0.a0"},
        url=(
            "https://conda.anaconda.org/conda-forge/linux-64/"
            "libzlib-1.3.2-h25fd6f3_2.conda"
        ),
        hash=HashModel(
            md5="d87ff7921124eccd67248aa483c23fec",
            sha256="55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
        ),
        categories={"main"},
    )
    locked = {"libzlib": healthy}

    def boom(**_kwargs):
        raise AssertionError("get_pkgs_dirs should not be called for clean lockfile")

    monkeypatch.setattr("conda_lock.solver.lockfile_heal.get_pkgs_dirs", boom)
    monkeypatch.setattr(
        "conda_lock.conda_solver._get_installed_conda_packages",
        lambda *_a, **_kw: {
            "libzlib": {
                "name": "libzlib",
                "version": "1.3.2",
                "channel": "conda-forge",
                "dist_name": "libzlib-1.3.2-h25fd6f3_2",
                "platform": "linux-64",
                "base_url": "https://conda.anaconda.org/conda-forge",
            }
        },
    )

    with caplog.at_level("WARNING", logger="conda_lock.conda_solver"):
        update_specs_for_arch(
            conda="/dummy",
            specs=[],
            locked=locked,
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
        )
    heal_warnings = [r for r in caplog.records if "Healed" in r.getMessage()]
    assert heal_warnings == []
