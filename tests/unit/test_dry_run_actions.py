"""Unit tests for ``conda_lock.solver.dry_run``.

Solver-output normalization concerns: rich-LINK to FETCH synthesis
(mamba 2.6.0 fast path), disk-fallback reconstruction for sparse
LINKs (older mamba/conda), and the WARNING that surfaces when the
degraded disk-fallback path is hit.

The dict literals model real mamba/conda JSON output, which is
dynamically typed at the boundary -- pretending they were TypedDicts
at every call site would be a wall of casts without catching real
bugs. The relevant arg-type checks are disabled file-wide.
"""

# mypy: disable-error-code="arg-type,comparison-overlap"

from __future__ import annotations

import json

from pathlib import Path

import pytest

from conda_lock.invoke_conda import conda_pkgs_dir
from conda_lock.solver.dry_run import (
    link_action_as_fetch,
    reconstruct_fetch_actions,
    warn_on_pkgs_dirs_leak,
)
from tests.support.fixtures import MAMBA_26_LINK_ACTION as _MAMBA_26_LINK_ACTION


TESTS_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# link_action_as_fetch
# ---------------------------------------------------------------------------


def test_link_action_as_fetch_uses_link_metadata():
    """Mamba 2.6.0 puts every FetchAction field in LINK; reuse it directly."""
    fetch = link_action_as_fetch(_MAMBA_26_LINK_ACTION)
    assert fetch is not None
    assert fetch["url"] == _MAMBA_26_LINK_ACTION["url"]
    assert fetch["sha256"] == _MAMBA_26_LINK_ACTION["sha256"]
    assert fetch["depends"] == _MAMBA_26_LINK_ACTION["depends"]
    assert fetch["constrains"] == _MAMBA_26_LINK_ACTION["constrains"]


def test_link_action_as_fetch_returns_none_for_sparse_link():
    """Older conda's LINK actions are sparse and need a disk lookup."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "channel": "conda-forge",
        "dist_name": "zlib-1.3.2-h25fd6f3_2",
        "name": "zlib",
        "platform": "linux-64",
        "version": "1.3.2",
    }
    assert link_action_as_fetch(sparse) is None


def test_link_action_as_fetch_requires_depends_field():
    """A LINK without ``depends`` would silently erase dependencies if we
    synthesized; reject it and force the disk fallback instead."""
    no_depends = {k: v for k, v in _MAMBA_26_LINK_ACTION.items() if k != "depends"}
    assert link_action_as_fetch(no_depends) is None
    null_depends = {**_MAMBA_26_LINK_ACTION, "depends": None}
    assert link_action_as_fetch(null_depends) is None


@pytest.mark.parametrize(
    "missing", ["md5", "url", "fn", "subdir", "channel", "version", "name", "timestamp"]
)
def test_link_action_as_fetch_requires_identity_field(missing: str):
    """Identity-bearing fields are mandatory for synthesis."""
    partial = {k: v for k, v in _MAMBA_26_LINK_ACTION.items() if k != missing}
    assert link_action_as_fetch(partial) is None


def test_link_action_as_fetch_rejects_corruption_signature():
    """The rich-LINK fast path bypasses ``get_repodata_record`` and
    therefore the ``is_mamba_2_1_to_2_3_stub_record`` check. Mamba 2.6.0+
    is supposed to heal cache records before emitting them in LINK,
    but a corrupted record passing through unhealed would otherwise
    ride straight into a synthesized FETCH, depending on an external
    invariant. We re-check the corruption signature in the fast path
    so the LINK-shaped corruption case routes to disk fallback (where
    ``heal_corrupt_record`` can recover from ``info/index.json``).
    """
    corrupt_link = {
        **_MAMBA_26_LINK_ACTION,
        "depends": [],  # corrupt mamba 2.1.1-2.3.3 zeroed this
        "license": "",  # ditto
        "timestamp": 0,  # ditto
    }
    # All the FETCH-shaped fields are present, so the *only* reason
    # this should be rejected is the corruption signature.
    assert link_action_as_fetch(corrupt_link) is None


# ---------------------------------------------------------------------------
# reconstruct_fetch_actions integration
# ---------------------------------------------------------------------------


def test_reconstruct_fetch_actions_synthesizes_from_link(monkeypatch):
    """When LINK contains all FETCH fields (mamba 2.6.0), no disk access is
    needed and ``get_pkgs_dirs`` must not be invoked."""

    def boom(**_kwargs):
        raise AssertionError("get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.solver.dry_run.get_pkgs_dirs", boom)

    dryrun = {
        "actions": {
            "LINK": [_MAMBA_26_LINK_ACTION],
            "FETCH": [],
        }
    }
    result = reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    assert len(result["actions"]["FETCH"]) == 1
    fetch = result["actions"]["FETCH"][0]
    assert fetch["name"] == "libzlib"
    assert fetch["url"] == _MAMBA_26_LINK_ACTION["url"]
    assert fetch["sha256"] == _MAMBA_26_LINK_ACTION["sha256"]


def test_reconstruct_fetch_actions_real_mamba_2_6_0_dryrun(monkeypatch):
    """Replay a real ``mamba 2.6.0`` LINK-only dryrun JSON.

    Captured by running ``mamba create --dry-run --json zlib`` against an
    already-populated ``CONDA_PKGS_DIRS`` so the solver has nothing to fetch.
    """

    def boom(**_kwargs):
        raise AssertionError("get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.solver.dry_run.get_pkgs_dirs", boom)

    fixture = (
        TESTS_DIR / "test-mamba-fixtures" / "dryrun-mamba-2.6.0-linux-64-zlib.json"
    )
    dryrun = json.loads(fixture.read_text())
    assert len(dryrun["actions"]["LINK"]) >= 1
    assert dryrun["actions"].get("FETCH", []) == []
    result = reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    fetched = result["actions"]["FETCH"]
    assert len(fetched) == len(dryrun["actions"]["LINK"])
    by_name = {f["name"]: f for f in fetched}
    for link in dryrun["actions"]["LINK"]:
        fetch = by_name[link["name"]]
        assert fetch["url"] == link["url"]
        assert fetch["sha256"] == link["sha256"]
        assert fetch["md5"] == link["md5"]
        assert fetch["depends"] == link["depends"]
        assert fetch["subdir"] == link["subdir"]


def test_reconstruct_fetch_actions_disk_fallback_on_hierarchical_cache(
    tmp_path: Path, monkeypatch
):
    """Drive the disk-fallback path with an on-disk hierarchical cache.

    Synthesis is rejected because the LINK is sparse (older-conda shape),
    so this exercises ``get_pkgs_dirs`` -> ``candidate_record_paths`` ->
    file open -> ``record_matches_link`` against a real cache directory
    laid out the way mamba 2.6.0 actually writes it.
    """
    fixture = (
        TESTS_DIR / "test-mamba-fixtures" / "dryrun-mamba-2.6.0-linux-64-zlib.json"
    )
    real = json.loads(fixture.read_text())
    real_link = real["actions"]["LINK"][0]
    dist_name = Path(real_link["fn"]).stem  # strip ".conda"

    pkgs_dir = tmp_path / "pkgs"
    record_dir = (
        pkgs_dir
        / "https/conda.anaconda.org/conda-forge"
        / real_link["subdir"]
        / dist_name
        / "info"
    )
    record_dir.mkdir(parents=True)
    record_path = record_dir / "repodata_record.json"
    record_path.write_text(json.dumps(real_link))

    sparse_link = {
        "name": real_link["name"],
        "version": real_link["version"],
        "platform": real_link["subdir"],
        "channel": real_link["channel"],
        "dist_name": dist_name,
        "fn": real_link["fn"],
        "md5": real_link["md5"],
        "sha256": real_link["sha256"],
        # No `depends`, no `timestamp` -> link_action_as_fetch returns None.
        "url": real_link["url"],
    }
    dryrun = {"actions": {"LINK": [sparse_link], "FETCH": []}}

    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [pkgs_dir],
    )

    result = reconstruct_fetch_actions("/dummy", real_link["subdir"], dryrun)
    assert len(result["actions"]["FETCH"]) == 1
    fetch = result["actions"]["FETCH"][0]
    assert fetch["url"] == real_link["url"]
    assert fetch["sha256"] == real_link["sha256"]

    # Plant impostor records (wrong sha256) at both hierarchical and flat
    # locations and assert validation rejects them.
    impostor_dir = (
        pkgs_dir
        / "https/repo.example.com/private"
        / real_link["subdir"]
        / dist_name
        / "info"
    )
    impostor_dir.mkdir(parents=True)
    (impostor_dir / "repodata_record.json").write_text(
        json.dumps({**real_link, "sha256": "deadbeef", "url": real_link["url"]})
    )
    flat_dir = pkgs_dir / dist_name / "info"
    flat_dir.mkdir(parents=True)
    (flat_dir / "repodata_record.json").write_text(
        json.dumps({**real_link, "sha256": "deadbeef"})
    )
    # Drop the URL so the hierarchical path can't be derived; falls back to flat.
    sparse_no_url = {k: v for k, v in sparse_link.items() if k != "url"}
    dryrun = {"actions": {"LINK": [sparse_no_url], "FETCH": []}}
    with pytest.raises(FileNotFoundError):
        reconstruct_fetch_actions("/dummy", real_link["subdir"], dryrun)


# --- 3. Degraded-path warning -------------------------------------------


def test_reconstruct_fetch_actions_warns_when_disk_fallback_is_used(
    tmp_path, monkeypatch, caplog
):
    """A sparse LINK forces the disk fallback. Even when the lookup
    succeeds, conda-lock emits a WARNING that this degraded path was
    taken so a corrupt cache (or stale mamba) is at least visible."""
    pkgs = tmp_path / "pkgs"
    info_dir = (
        pkgs
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
    )
    info_dir.mkdir(parents=True)
    (info_dir / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "depends": ["__glibc >=2.17"],
                "license": "Zlib",
                "subdir": "linux-64",
                "timestamp": 1774072609,
                "url": _MAMBA_26_LINK_ACTION["url"],
                "md5": _MAMBA_26_LINK_ACTION["md5"],
                "sha256": _MAMBA_26_LINK_ACTION["sha256"],
                "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
                "channel": "conda-forge",
            }
        )
    )
    sparse_link = {
        "name": "libzlib",
        "version": "1.3.2",
        "platform": "linux-64",
        "channel": "conda-forge",
        "dist_name": "libzlib-1.3.2-h25fd6f3_2",
        "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
        "url": _MAMBA_26_LINK_ACTION["url"],
    }
    dryrun = {"actions": {"LINK": [sparse_link], "FETCH": []}}
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [pkgs],
    )
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        result = reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    assert len(result["actions"]["FETCH"]) == 1
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "Reconstructing FETCH actions from the package cache" in msgs
    assert "libzlib" in msgs


# ---------------------------------------------------------------------------
# RepodataLookup -> warning translation (orchestration boundary)
# ---------------------------------------------------------------------------
#
# The cache layer (``conda_lock.solver.repodata_cache``) is silent at
# WARNING level by contract: it returns a structured ``RepodataLookup``
# and ``reconstruct_fetch_actions`` here decides what to log. These
# tests pin the translation so a future "let's just have the cache
# layer warn directly" mistake fails loudly instead of regressing
# the layering.


def _sparse_link_action() -> dict:
    """A LINK shaped like older-conda output, sparse enough that
    ``link_action_as_fetch`` rejects it and we drop into the
    disk-fallback path where ``get_repodata_record`` is consulted."""
    return {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "channel": "conda-forge",
        "dist_name": "libzlib-1.3.2-h25fd6f3_2",
        "name": "libzlib",
        "platform": "linux-64",
        "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
        "version": "1.3.2",
    }


def test_reconstruct_fetch_actions_warns_with_remediation_on_healed(
    monkeypatch, tmp_path, caplog
):
    """``RepodataLookup(outcome="healed")`` -> WARNING with
    ``mamba clean -a`` remediation text, FETCH appended, no
    exception."""
    from conda_lock.solver.dry_run import reconstruct_fetch_actions
    from conda_lock.solver.repodata_cache import RepodataLookup

    healed_record = {
        "name": "libzlib",
        "version": "1.3.2",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda",
        "md5": "d87ff7921124eccd67248aa483c23fec",
        "sha256": "55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
        "depends": ["__glibc >=2.17,<3.0.a0"],
        "subdir": "linux-64",
        "channel": "conda-forge",
        "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
    }
    healed_from = (
        tmp_path / "pkgs/.../libzlib-1.3.2-h25fd6f3_2/info/repodata_record.json"
    )
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "pkgs"],
    )
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_repodata_record",
        lambda *_args, **_kwargs: RepodataLookup(
            record=healed_record, outcome="healed", healed_from=healed_from
        ),
    )
    dryrun = {"actions": {"LINK": [_sparse_link_action()], "FETCH": []}}
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        result = reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    fetched = result["actions"]["FETCH"]
    assert len(fetched) == 1
    assert fetched[0]["depends"] == ["__glibc >=2.17,<3.0.a0"]
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "Healed corrupt repodata_record.json" in msgs
    assert "mamba clean -a" in msgs
    assert "896" in msgs


def test_reconstruct_fetch_actions_raises_with_warning_on_unhealable_corrupt(
    monkeypatch, tmp_path, caplog
):
    """``RepodataLookup(outcome="unhealable_corrupt")`` -> WARNING
    naming the corruption signature plus
    ``regenerate from sources`` remediation, then
    ``FileNotFoundError`` so the lock attempt fails loudly rather
    than producing a silently-incomplete plan."""
    from conda_lock.solver.dry_run import reconstruct_fetch_actions
    from conda_lock.solver.repodata_cache import RepodataLookup

    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "pkgs"],
    )
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_repodata_record",
        lambda *_args, **_kwargs: RepodataLookup(
            record=None,
            outcome="unhealable_corrupt",
            reason=(
                "corrupt record at /pkgs/.../info/repodata_record.json "
                "(mamba 2.1.1-2.3.3 signature) and info/index.json "
                "missing -- cannot heal"
            ),
        ),
    )
    dryrun = {"actions": {"LINK": [_sparse_link_action()], "FETCH": []}}
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        with pytest.raises(FileNotFoundError):
            reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    # This is the orchestration boundary contract for
    # ``unhealable_corrupt``. Pin every required substring
    # individually -- weakening any of these to "or" with
    # "mamba clean -a" would let the contract drift silently.
    assert "mamba 2.1.1-2.3.3" in msgs
    assert "info/index.json" in msgs
    assert "Regenerate from sources" in msgs
    assert "mamba clean -a" in msgs
    assert "896" in msgs


def test_reconstruct_fetch_actions_raises_with_warning_on_not_found(
    monkeypatch, tmp_path, caplog
):
    """``RepodataLookup(outcome="not_found")`` -> WARNING with the
    diagnostic ``reason``, then ``FileNotFoundError``. The reason
    text comes from the cache layer's most-actionable rejection
    description (e.g. ``identity mismatch ... sha256``), surfaced
    verbatim through orchestration."""
    from conda_lock.solver.dry_run import reconstruct_fetch_actions
    from conda_lock.solver.repodata_cache import RepodataLookup

    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "pkgs"],
    )
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_repodata_record",
        lambda *_args, **_kwargs: RepodataLookup(
            record=None,
            outcome="not_found",
            reason="identity mismatch at /tmp/foo: sha256 mismatch",
        ),
    )
    dryrun = {"actions": {"LINK": [_sparse_link_action()], "FETCH": []}}
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        with pytest.raises(FileNotFoundError):
            reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "Failed to find repodata_record.json" in msgs
    assert "identity mismatch" in msgs
    assert "sha256" in msgs


def test_reconstruct_fetch_actions_silent_on_found_outcome(
    monkeypatch, tmp_path, caplog
):
    """``RepodataLookup(outcome="found")`` -> no WARNING from the
    orchestration layer. The disk-fallback degraded-path warning
    fires earlier (whenever any LINK is sparse), but the per-package
    fallback success itself is not warning-worthy."""
    from conda_lock.solver.dry_run import reconstruct_fetch_actions
    from conda_lock.solver.repodata_cache import RepodataLookup

    found_record = {
        "name": "libzlib",
        "version": "1.3.2",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda",
        "md5": "d87ff7921124eccd67248aa483c23fec",
        "sha256": "55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
        "depends": ["__glibc >=2.17,<3.0.a0"],
        "subdir": "linux-64",
        "channel": "conda-forge",
        "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
    }
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "pkgs"],
    )
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_repodata_record",
        lambda *_args, **_kwargs: RepodataLookup(record=found_record, outcome="found"),
    )
    dryrun = {"actions": {"LINK": [_sparse_link_action()], "FETCH": []}}
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        result = reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
    assert len(result["actions"]["FETCH"]) == 1
    # The only WARNING-level message permitted here is the
    # generic degraded-disk-fallback breadcrumb, NOT a per-package
    # success line.
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "Healed" not in msgs
    assert "Failed to find" not in msgs
    assert "info/index.json" not in msgs


# ---------------------------------------------------------------------------
# pkgs_dirs leak detection
# ---------------------------------------------------------------------------


def test_warn_on_pkgs_dirs_leak_emits_warning_for_extras(caplog):
    """Anything beyond the conda-lock isolated cache dir is a leak."""
    expected = Path(conda_pkgs_dir())
    leaked = Path("/home/user/.condarc-leaked-cache")
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        warn_on_pkgs_dirs_leak([expected, leaked])
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "Extra pkgs_dirs leaked from user config" in msgs
    assert str(leaked) in msgs


def test_warn_on_pkgs_dirs_leak_quiet_when_only_expected(caplog):
    """No warning when the solver's pkgs_dirs is exactly our temp dir."""
    expected = Path(conda_pkgs_dir())
    with caplog.at_level("WARNING", logger="conda_lock.solver.dry_run"):
        warn_on_pkgs_dirs_leak([expected])
    leak_warnings = [r for r in caplog.records if "leaked" in r.getMessage()]
    assert leak_warnings == []
