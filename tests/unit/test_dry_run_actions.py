"""Tests for rich LINK synthesis, sparse LINK verification and metadata warnings."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from conda_lock.errors import MetadataConsistencyError
from conda_lock.models.channel import Channel
from conda_lock.solver.dry_run import (
    link_action_as_fetch,
    reconstruct_fetch_actions_in_place,
)
from tests.support.fixtures import (
    CONDA_LINK_ACTION,
    MAMBA_26_LINK_ACTION,
    MICROMAMBA_1_5_LINK_ACTION,
)


@pytest.fixture(autouse=True)
def captured_channel_metadata(monkeypatch):
    # Independent channel evidence for the disk-fallback fixtures below.
    monkeypatch.setattr(
        "conda_lock.solver.channel_metadata.query_channel_records",
        lambda *args, **kwargs: [
            dict(MAMBA_26_LINK_ACTION),
            {
                **MAMBA_26_LINK_ACTION,
                "fn": MAMBA_26_LINK_ACTION["fn"].replace(".conda", ".tar.bz2"),
                "url": MAMBA_26_LINK_ACTION["url"].replace(".conda", ".tar.bz2"),
            },
        ],
    )


TESTS_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("sparse", [False, True])
def test_private_actions_match_cache_and_placeholder_urls(
    tmp_path, monkeypatch, sparse
):
    monkeypatch.setenv("CHANNEL_TOKEN", "test-secret")
    channel = Channel.from_string("https://example.org/t/$CHANNEL_TOKEN/private")
    record = {
        **MAMBA_26_LINK_ACTION,
        "url": f"{channel.env_replaced_url()}/linux-64/{MAMBA_26_LINK_ACTION['fn']}",
    }
    link = {**record, "url": record["url"].replace("test-secret", "**********")}
    if sparse:
        link.pop("depends")
        dist_name = record["fn"].removesuffix(".conda")
        info = tmp_path / dist_name / "info"
        info.mkdir(parents=True)
        (info / "repodata_record.json").write_text(json.dumps(record))
        monkeypatch.setattr(
            "conda_lock.solver.dry_run.get_pkgs_dirs", lambda **kw: [tmp_path]
        )
        monkeypatch.setattr(
            "conda_lock.solver.channel_metadata.query_channel_records",
            lambda *args, **kw: [record],
        )
        fetches = []
    else:
        fetches = [
            {**record, "url": record["url"].replace("test-secret", "${CHANNEL_TOKEN}")}
        ]
    plan = {"actions": {"LINK": [link], "FETCH": fetches}}
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", plan, channels=[channel])
    assert len(plan["actions"]["FETCH"]) == 1
    assert plan["actions"]["FETCH"][0]["depends"] == record["depends"]
    assert plan["actions"]["LINK"][0]["url"] == link["url"]


# ---------------------------------------------------------------------------
# link_action_as_fetch
# ---------------------------------------------------------------------------


def test_link_action_as_fetch_uses_link_metadata():
    """Mamba-family solvers put every FetchAction field in LINK; reuse it directly."""
    fetch = link_action_as_fetch(MAMBA_26_LINK_ACTION)
    assert fetch is not None
    assert fetch["url"] == MAMBA_26_LINK_ACTION["url"]
    assert fetch["sha256"] == MAMBA_26_LINK_ACTION["sha256"]
    assert fetch["depends"] == MAMBA_26_LINK_ACTION["depends"]
    assert fetch["constrains"] == MAMBA_26_LINK_ACTION["constrains"]


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


def test_link_action_as_fetch_accepts_micromamba_1_5_link():
    """The rich-LINK fast path is not a mamba 2.6 feature: micromamba
    1.5.12 already emits the full repodata record in LINK (verified by
    capture; see ``tests/support/fixtures.py``)."""
    fetch = link_action_as_fetch(MICROMAMBA_1_5_LINK_ACTION)
    assert fetch is not None
    assert fetch["url"] == MICROMAMBA_1_5_LINK_ACTION["url"]
    assert fetch["sha256"] == MICROMAMBA_1_5_LINK_ACTION["sha256"]
    assert fetch["depends"] == MICROMAMBA_1_5_LINK_ACTION["depends"]


def test_link_action_as_fetch_rejects_real_conda_link():
    """conda (captured: 26.5.2) and the Python ``mamba`` 1.x CLI emit
    the sparse conda-meta LINK shape with no dependency or identity
    fields; the fast path must decline so the disk fallback runs."""
    assert link_action_as_fetch(CONDA_LINK_ACTION) is None


def test_link_action_as_fetch_requires_depends_field():
    """A LINK without ``depends`` would silently erase dependencies if we
    synthesized; reject it and force the disk fallback instead."""
    no_depends = {k: v for k, v in MAMBA_26_LINK_ACTION.items() if k != "depends"}
    assert link_action_as_fetch(no_depends) is None
    null_depends = {**MAMBA_26_LINK_ACTION, "depends": None}
    assert link_action_as_fetch(null_depends) is None
    # Present but not a list (e.g. a stray string) is equally unusable.
    non_list_depends = {**MAMBA_26_LINK_ACTION, "depends": "__glibc >=2.17"}
    assert link_action_as_fetch(non_list_depends) is None


@pytest.mark.parametrize(
    "missing", ["md5", "url", "fn", "subdir", "channel", "version", "name", "timestamp"]
)
def test_link_action_as_fetch_requires_identity_field(missing: str):
    """Identity-bearing fields are mandatory for synthesis."""
    partial = {k: v for k, v in MAMBA_26_LINK_ACTION.items() if k != missing}
    assert link_action_as_fetch(partial) is None


# ---------------------------------------------------------------------------
# reconstruct_fetch_actions_in_place integration
# ---------------------------------------------------------------------------


def test_reconstruct_fetch_actions_synthesizes_from_link(monkeypatch):
    """When LINK contains all FETCH fields (mamba 2.x/micromamba), no disk access is
    needed and ``get_pkgs_dirs`` must not be invoked."""

    def boom(**_kwargs):
        raise AssertionError("get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.solver.dry_run.get_pkgs_dirs", boom)

    dryrun = {
        "actions": {
            "LINK": [MAMBA_26_LINK_ACTION],
            "FETCH": [],
        }
    }
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    assert len(dryrun["actions"]["FETCH"]) == 1
    fetch = dryrun["actions"]["FETCH"][0]
    assert fetch["name"] == "libzlib"
    assert fetch["url"] == MAMBA_26_LINK_ACTION["url"]
    assert fetch["sha256"] == MAMBA_26_LINK_ACTION["sha256"]


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
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    fetched = dryrun["actions"]["FETCH"]
    assert len(fetched) == len(dryrun["actions"]["LINK"])
    by_name = {f["name"]: f for f in fetched}
    for link in dryrun["actions"]["LINK"]:
        fetch = by_name[link["name"]]
        assert fetch["url"] == link["url"]
        assert fetch["sha256"] == link["sha256"]
        assert fetch["md5"] == link["md5"]
        assert fetch["depends"] == link["depends"]
        assert fetch["subdir"] == link["subdir"]


def test_reconstruct_fetch_actions_real_micromamba_1_5_dryrun(monkeypatch):
    """Replay a real ``micromamba 1.5.12`` LINK-only dryrun JSON.

    Pre-2.6 micromamba already emits rich LINK actions, so the fast
    path must synthesize every FETCH without touching the disk.
    """

    def boom(**_kwargs):
        raise AssertionError("get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.solver.dry_run.get_pkgs_dirs", boom)

    fixture = (
        TESTS_DIR
        / "test-mamba-fixtures"
        / "dryrun-micromamba-1.5.12-linux-64-libzlib.json"
    )
    dryrun = json.loads(fixture.read_text())
    assert dryrun["actions"].get("FETCH") in (None, [])
    link = dryrun["actions"]["LINK"][0]
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    fetched = dryrun["actions"]["FETCH"]
    assert [f["name"] for f in fetched] == ["libzlib"]
    assert fetched[0]["url"] == link["url"]
    assert fetched[0]["sha256"] == link["sha256"]
    assert fetched[0]["depends"] == link["depends"]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "dryrun-conda-26.5.2-linux-64-libzlib.json",
        "dryrun-mamba-1.5.12-python-linux-64-libzlib.json",
    ],
)
def test_reconstruct_fetch_actions_real_sparse_dryrun_uses_disk(
    tmp_path: Path, monkeypatch, fixture_name: str
):
    """Replay real ``conda`` / Python ``mamba`` 1.x LINK-only dryruns.

    These solvers emit the sparse conda-meta LINK shape (no
    ``depends``, no artifact identity), so reconstruction must read
    ``repodata_record.json`` from the package cache -- flat layout,
    as those solvers write it.
    """
    fixture = TESTS_DIR / "test-mamba-fixtures" / fixture_name
    dryrun = json.loads(fixture.read_text())
    link = dryrun["actions"]["LINK"][0]
    dist_name = link["dist_name"]

    record = dict(MAMBA_26_LINK_ACTION)
    flat_dir = tmp_path / "pkgs" / dist_name / "info"
    flat_dir.mkdir(parents=True)
    (flat_dir / "repodata_record.json").write_text(json.dumps(record))
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "pkgs"],
    )

    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    fetched = dryrun["actions"]["FETCH"]
    assert [f["name"] for f in fetched] == ["libzlib"]
    assert fetched[0]["depends"] == record["depends"]
    assert fetched[0]["sha256"] == record["sha256"]


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

    reconstruct_fetch_actions_in_place(
        "/dummy", real_link["subdir"], dryrun, channels=[]
    )
    assert len(dryrun["actions"]["FETCH"]) == 1
    fetch = dryrun["actions"]["FETCH"][0]
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
    with pytest.raises(MetadataConsistencyError, match="No valid cache record"):
        reconstruct_fetch_actions_in_place(
            "/dummy", real_link["subdir"], dryrun, channels=[]
        )


def test_reconstruct_fetch_actions_creates_missing_action_keys():
    """A dryrun without LINK/FETCH keys is normalized, not crashed on."""
    dryrun = {"actions": {}}
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    assert dryrun["actions"] == {"LINK": [], "FETCH": []}


@pytest.mark.parametrize(
    ("fn", "expected_dist_name"),
    [
        ("libzlib-1.3.2-h25fd6f3_2.conda", "libzlib-1.3.2-h25fd6f3_2"),
        ("libzlib-1.3.2-h25fd6f3_2.tar.bz2", "libzlib-1.3.2-h25fd6f3_2"),
    ],
)
def test_reconstruct_fetch_actions_derives_dist_name_from_fn(
    tmp_path: Path, monkeypatch, fn: str, expected_dist_name: str
):
    """A LINK without ``dist_name`` (the mamba rich-LINK shape) that gets
    deferred to disk fallback derives the cache dirname from ``fn``,
    stripping either archive extension."""
    # Keep record and LINK consistent (same fn/url) so the identity
    # gate is not what this test exercises.
    url = "https://conda.anaconda.org/conda-forge/linux-64/" + fn
    record = {**MAMBA_26_LINK_ACTION, "fn": fn, "url": url}
    flat_dir = tmp_path / "pkgs" / expected_dist_name / "info"
    flat_dir.mkdir(parents=True)
    (flat_dir / "repodata_record.json").write_text(json.dumps(record))
    monkeypatch.setattr(
        "conda_lock.solver.dry_run.get_pkgs_dirs",
        lambda **_kwargs: [tmp_path / "pkgs"],
    )
    # No dist_name, no depends -> fast path declines, disk fallback runs.
    link = {k: v for k, v in record.items() if k not in ("depends",)}
    dryrun = {"actions": {"LINK": [link], "FETCH": []}}
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    assert dryrun["actions"]["FETCH"][0]["depends"] == record["depends"]


def test_reconstruct_fetch_actions_rejects_unknown_filename_format(monkeypatch):
    """An undeferrable LINK (unknown archive extension) is a hard error,
    not a silent skip."""
    monkeypatch.setattr("conda_lock.solver.dry_run.get_pkgs_dirs", lambda **_kwargs: [])
    link = {"name": "weird", "fn": "weird-1.0-0.zip"}
    dryrun = {"actions": {"LINK": [link], "FETCH": []}}
    with pytest.raises(ValueError, match="Unknown filename format"):
        reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])


def test_reconstruct_fetch_actions_requires_dist_name_or_fn(monkeypatch):
    """A LINK exposing neither ``dist_name`` nor ``fn`` cannot be looked
    up on disk at all."""
    monkeypatch.setattr("conda_lock.solver.dry_run.get_pkgs_dirs", lambda **_kwargs: [])
    dryrun = {"actions": {"LINK": [{"name": "mystery"}], "FETCH": []}}
    with pytest.raises(ValueError, match="Unable to extract the dist_name"):
        reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])


@pytest.mark.parametrize(
    "timestamp, license, warns", [(0, "", True), (1, "", False), (0, "MIT", False)]
)
def test_link_metadata_fingerprint_warns_without_changing_dependencies(
    caplog, timestamp, license, warns
):
    record = {
        **MAMBA_26_LINK_ACTION,
        "timestamp": timestamp,
        "license": license,
        "depends": [],
    }
    dryrun = {"actions": {"FETCH": [], "LINK": [dict(record)]}}
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    assert dryrun["actions"]["FETCH"][0]["depends"] == []
    assert link_action_as_fetch(record) is not None
    assert ("timestamp=0 and an empty license" in caplog.text) is warns
    if warns:
        assert "libzlib" in caplog.text
        assert "without --update" in caplog.text


def test_fetch_metadata_with_empty_license_and_timestamp_is_accepted(caplog):
    record = {**MAMBA_26_LINK_ACTION, "timestamp": 0, "license": "", "depends": []}
    dryrun = {"actions": {"FETCH": [record], "LINK": [dict(record)]}}
    reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
    assert dryrun["actions"]["FETCH"] == [record]
    assert not caplog.records


@pytest.mark.parametrize("field, value", [("depends", []), ("md5", "other-digest")])
def test_conflicting_live_actions_are_rejected(field, value):
    dryrun = {
        "actions": {
            "FETCH": [dict(MAMBA_26_LINK_ACTION)],
            "LINK": [{**MAMBA_26_LINK_ACTION, field: value}],
        }
    }
    with pytest.raises(MetadataConsistencyError, match="FETCH/LINK"):
        reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])


@pytest.mark.parametrize("version", ["2.6.0", "2.7.0", "2.8.0", None])
def test_null_dependencies_are_only_normalized_for_known_live_protocol(
    monkeypatch, version
):
    from packaging.version import Version

    monkeypatch.setattr(
        "conda_lock.solver.dry_run.mamba_binary_version",
        lambda _: Version(version) if version else None,
    )
    record = {**MAMBA_26_LINK_ACTION, "depends": None}
    dryrun = {"actions": {"FETCH": [record]}}
    if version in {"2.6.0", "2.7.0"}:
        reconstruct_fetch_actions_in_place("/dummy", "linux-64", dryrun, channels=[])
        assert dryrun["actions"]["FETCH"][0]["depends"] == []
    else:
        with pytest.raises(MetadataConsistencyError, match="depends must be"):
            reconstruct_fetch_actions_in_place(
                "/dummy", "linux-64", dryrun, channels=[]
            )
