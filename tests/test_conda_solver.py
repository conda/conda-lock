"""Tests for the cache-record reconstruction helpers in `conda_lock.conda_solver`.

Mostly white-box tests for the bits that translate mamba/conda dryrun
output into FetchAction records. Kept separate from `test_conda_lock.py`
so the helper-level coverage doesn't drown in the broader integration
suite.
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

import pytest

from conda_lock.conda_solver import (
    _candidate_record_paths,
    _get_repodata_record,
    _hierarchical_cache_subpath,
    _link_action_as_fetch,
    _normalize_url_for_compare,
    _record_matches_link,
    _reconstruct_fetch_actions,
    _libmamba_strip_url_secrets,
)


TESTS_DIR = Path(__file__).parent

# Fields exactly as a real `mamba 2.6.0` dryrun emits them in `LINK`.
_MAMBA_26_LINK_ACTION = {
    "build": "h25fd6f3_2",
    "build_number": 2,
    "build_string": "h25fd6f3_2",
    "channel": "conda-forge",
    "constrains": ["zlib 1.3.2 *_2"],
    "depends": ["__glibc >=2.17,<3.0.a0"],
    "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
    "license": "Zlib",
    "md5": "d87ff7921124eccd67248aa483c23fec",
    "name": "libzlib",
    "sha256": "55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
    "size": 63629,
    "subdir": "linux-64",
    "timestamp": 1774072609,
    "track_features": "",
    "url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda",
    "version": "1.3.2",
}


def _matched(record: dict, link: dict) -> bool:
    matched, _reason = _record_matches_link(record, link)
    return matched


def _write_record(path: Path, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields))


# ---------------------------------------------------------------------------
# _link_action_as_fetch
# ---------------------------------------------------------------------------


def test_link_action_as_fetch_uses_link_metadata():
    """Mamba 2.6.0 puts every FetchAction field in LINK; reuse it directly."""
    fetch = _link_action_as_fetch(_MAMBA_26_LINK_ACTION)
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
    assert _link_action_as_fetch(sparse) is None


def test_link_action_as_fetch_requires_depends_field():
    """A LINK without ``depends`` would silently erase dependencies if we
    synthesized; reject it and force the disk fallback instead."""
    no_depends = {k: v for k, v in _MAMBA_26_LINK_ACTION.items() if k != "depends"}
    assert _link_action_as_fetch(no_depends) is None
    null_depends = {**_MAMBA_26_LINK_ACTION, "depends": None}
    assert _link_action_as_fetch(null_depends) is None


@pytest.mark.parametrize(
    "missing", ["md5", "url", "fn", "subdir", "channel", "version", "name", "timestamp"]
)
def test_link_action_as_fetch_requires_identity_field(missing: str):
    """Identity-bearing fields are mandatory for synthesis."""
    partial = {k: v for k, v in _MAMBA_26_LINK_ACTION.items() if k != missing}
    assert _link_action_as_fetch(partial) is None


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_libmamba_strip_url_secrets_handles_no_scheme_credentials():
    """libmamba removes ``user:pass@`` and ``/t/<token>`` even on URLs
    without a scheme (see ``test_cpp.cpp`` ``URLs without scheme``)."""
    assert (
        _libmamba_strip_url_secrets("root:secretpassword@myweb.com/test.repo")
        == "myweb.com/test.repo"
    )
    assert (
        _libmamba_strip_url_secrets("myweb.com/t/my-12345-token/test.repo")
        == "myweb.com/test.repo"
    )
    # Don't get fooled by '@' that appears after the path separator.
    assert (
        _libmamba_strip_url_secrets("myweb.com/path/with@in-it/foo")
        == "myweb.com/path/with@in-it/foo"
    )


def test_libmamba_strip_url_secrets_token_without_trailing_slash():
    """libmamba's token regex matches ``/t/<token>`` whether or not there's
    a trailing path component; the previous regex required a trailing
    slash, leaving terminal-token URLs untouched."""
    assert _libmamba_strip_url_secrets("https://repo.com/t/my-token") == "https://repo.com"
    assert (
        _libmamba_strip_url_secrets("https://repo.com/t/my-token/path")
        == "https://repo.com/path"
    )


def test_libmamba_strip_url_secrets_token_regex_is_intentionally_overreaching():
    """Pin the slightly weird-looking behavior so a future cleanup
    doesn't quietly diverge from libmamba's cache path derivation.

    libmamba's regex (``/t/([a-zA-Z0-9-_]{0,2}[a-zA-Z0-9-]*)``) treats
    *any* ``/t/<chars>`` path component as an auth token and removes it.
    A path like ``/not/t/pkg.conda`` therefore loses the ``/t/pkg``
    segment. We deliberately match that behavior so our cache lookup
    derives the same path mamba 2.6.0 wrote -- correctness with respect
    to the cache, not generic URL sanitization.
    """
    assert (
        _libmamba_strip_url_secrets("https://repo.com/not/t/pkg.conda")
        == "https://repo.com/not.conda"
    )


def test_normalize_url_for_compare_force_https_strip_slash():
    """The normalized form drops scheme differences and trailing slashes."""
    assert _normalize_url_for_compare(
        "http://conda.example.com/c/linux-64/foo.conda"
    ) == _normalize_url_for_compare(
        "https://conda.example.com/c/linux-64/foo.conda/"
    )
    assert _normalize_url_for_compare(
        "https://user:pw@conda.example.com/c/linux-64/foo.conda"
    ) == _normalize_url_for_compare(
        "https://conda.example.com/c/linux-64/foo.conda"
    )


# ---------------------------------------------------------------------------
# _hierarchical_cache_subpath
# ---------------------------------------------------------------------------


def test_hierarchical_cache_subpath_from_url():
    """The cache subpath is derived from the package URL, not a glob."""
    sub = _hierarchical_cache_subpath(_MAMBA_26_LINK_ACTION)
    assert sub == Path("https/conda.anaconda.org/conda-forge/linux-64")


def test_hierarchical_cache_subpath_from_base_url_and_platform():
    """Falls back to base_url + platform when the URL is absent."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "platform": "linux-64",
    }
    assert _hierarchical_cache_subpath(sparse) == Path(
        "https/conda.anaconda.org/conda-forge/linux-64"
    )


def test_hierarchical_cache_subpath_strips_duplicated_platform_suffix():
    """``base_url`` may already include the subdir; don't double it up."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge/linux-64",
        "platform": "linux-64",
    }
    assert _hierarchical_cache_subpath(sparse) == Path(
        "https/conda.anaconda.org/conda-forge/linux-64"
    )


def test_hierarchical_cache_subpath_normalizes_port_separator():
    """Per mamba 2.6.0, a port's ``:`` is escaped to ``_``."""
    link = {"url": "http://localhost:8000/mychannel/noarch/foo-1.0-bld.conda"}
    assert _hierarchical_cache_subpath(link) == Path(
        "http/localhost_8000/mychannel/noarch"
    )


def test_hierarchical_cache_subpath_strips_credentials():
    """Mamba feeds ``remove_secrets_and_login_credentials`` *before* path
    normalization, so userinfo and ``/t/<token>/`` segments must not appear
    in the cache path. Otherwise the fallback misses authenticated channels
    even when mamba cached the package correctly."""
    with_userinfo = {
        "url": "https://user:secret@conda.example.com/private/linux-64/foo.conda"
    }
    assert _hierarchical_cache_subpath(with_userinfo) == Path(
        "https/conda.example.com/private/linux-64"
    )
    with_token = {
        "url": "https://conda.anaconda.org/t/aa-bbb-ccc/private/linux-64/foo.conda"
    }
    assert _hierarchical_cache_subpath(with_token) == Path(
        "https/conda.anaconda.org/private/linux-64"
    )


# ---------------------------------------------------------------------------
# _record_matches_link
# ---------------------------------------------------------------------------


def test_record_matches_link_validates_identity():
    record = {
        "name": "zlib",
        "version": "1.3.2",
        "subdir": "linux-64",
        "sha256": "abc",
        "md5": "111",
        "fn": "zlib-1.3.2-bld.conda",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-bld.conda",
        "channel": "conda-forge",
    }
    link = {
        "name": "zlib",
        "version": "1.3.2",
        "platform": "linux-64",
        "sha256": "abc",
        "md5": "111",
        "fn": "zlib-1.3.2-bld.conda",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-bld.conda",
        "channel": "conda-forge",
    }
    assert _matched(record, link)
    # Every concrete identity field is enforced.
    assert not _matched({**record, "version": "9.9.9"}, link)
    assert not _matched({**record, "subdir": "osx-64"}, link)
    assert not _matched({**record, "sha256": "deadbeef"}, link)
    assert not _matched({**record, "md5": "deadbeef"}, link)
    assert not _matched({**record, "fn": "other.conda"}, link)
    assert not _matched(
        {**record, "url": "https://other.example.com/c/linux-64/x.conda"}, link
    )


def test_record_matches_link_url_takes_precedence_over_channel():
    """libmamba: when the record carries a URL, validate the URL and DON'T
    fall back to channel-string compare. Mirrored / aliased channels can
    legitimately spell their channel string differently while pointing at
    the same artifact URL."""
    name_version = {"name": "foo", "version": "1.0"}
    url = "https://conda.anaconda.org/conda-forge/linux-64/foo.conda"
    record = {**name_version, "url": url, "channel": "https://conda.anaconda.org/conda-forge"}
    link = {**name_version, "url": url, "channel": "conda-forge"}
    assert _matched(record, link)


def test_record_matches_link_falls_back_to_channel_when_record_url_empty():
    """libmamba: only when the record has no URL, validate the channel
    string. A channel mismatch in that fallback DOES reject."""
    name_version = {"name": "foo", "version": "1.0"}
    record = {**name_version, "channel": "conda-forge"}
    link = {**name_version, "channel": "conda-forge"}
    assert _matched(record, link)
    assert not _matched({**name_version, "channel": "other"}, link)


def test_record_matches_link_falls_back_to_channel_when_link_url_unavailable():
    """A sparse old-conda LINK action may have no ``url`` and no
    ``base_url``/``fn`` to derive one from. Previously, when the disk
    record HAD a url, we accepted the record without comparing channels
    -- a real validation gap. Now: if URL can't be compared on both
    sides, fall back to channel; mismatched channel rejects."""
    name_version = {"name": "foo", "version": "1.0"}
    record = {
        **name_version,
        "url": "https://conda.anaconda.org/conda-forge/linux-64/foo.conda",
        "channel": "conda-forge",
    }
    sparse_link_same_channel = {**name_version, "channel": "conda-forge"}
    assert _matched(record, sparse_link_same_channel)
    sparse_link_other_channel = {**name_version, "channel": "other-channel"}
    matched, reason = _record_matches_link(record, sparse_link_other_channel)
    assert not matched
    assert reason and "channel mismatch" in reason


def test_record_matches_link_uses_derived_url_from_base_url_and_fn():
    """When the LINK has ``base_url`` + ``fn`` but no explicit ``url``,
    derive one and compare against the record's URL."""
    record = {
        "name": "foo",
        "version": "1.0",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/foo-1.0-bld.conda",
        "channel": "conda-forge",
    }
    link = {
        "name": "foo",
        "version": "1.0",
        "base_url": "https://conda.anaconda.org/conda-forge",
        "platform": "linux-64",
        "fn": "foo-1.0-bld.conda",
        # NB: channel intentionally spelled differently from record's; the
        # derived URL match should mean we DON'T fall back to channel and
        # therefore don't reject for the spelling difference.
        "channel": "https://conda.anaconda.org/conda-forge",
    }
    assert _matched(record, link)
    # But a derived URL that doesn't match the record's URL DOES reject.
    bad_link = {
        **link,
        "base_url": "https://other.example.com/conda-forge",
    }
    matched, reason = _record_matches_link(record, bad_link)
    assert not matched
    assert reason and "url mismatch" in reason


def test_record_matches_link_ignores_credentials_in_url_compare():
    """A record fetched via tokenized URL must still validate against an
    untokenized LINK URL (and vice versa)."""
    record = {
        "name": "foo",
        "version": "1.0",
        "url": "https://conda.example.com/t/SECRET/private/linux-64/foo.conda",
    }
    link = {
        "name": "foo",
        "version": "1.0",
        "url": "https://conda.example.com/private/linux-64/foo.conda",
    }
    assert _matched(record, link)


def test_record_matches_link_url_compare_matches_libmamba():
    """libmamba's ``compare_cleaned_url`` parses both URLs, forces scheme
    to ``https``, strips credentials, and trims trailing slashes before
    comparing. Differences libmamba considers equivalent must not reject."""
    name_version = {"name": "foo", "version": "1.0"}
    record = {**name_version, "url": "http://conda.example.com/c/linux-64/foo.conda"}
    link = {**name_version, "url": "https://conda.example.com/c/linux-64/foo.conda"}
    assert _matched(record, link)
    record = {**name_version, "url": "https://conda.example.com/c/linux-64/foo.conda/"}
    link = {**name_version, "url": "https://conda.example.com/c/linux-64/foo.conda"}
    assert _matched(record, link)
    record = {
        **name_version,
        "url": "https://user:pass@conda.example.com/c/linux-64/foo.conda",
    }
    link = {**name_version, "url": "https://conda.example.com/c/linux-64/foo.conda"}
    assert _matched(record, link)


def test_record_matches_link_rejects_when_one_side_missing_identity():
    """If validation fields are absent on both sides, only name+version are
    checked. That's a known compromise for very old metadata."""
    record = {"name": "foo", "version": "1.0"}
    link = {"name": "foo", "version": "1.0"}
    assert _matched(record, link)
    assert not _matched({"name": "bar", "version": "1.0"}, link)


def test_record_matches_link_returns_reason_for_diagnostic():
    """Failure reasons should be specific so the production log isn't an
    archaeology project later."""
    record = {"name": "foo", "version": "1.0", "sha256": "abc"}
    link = {"name": "foo", "version": "1.0", "sha256": "deadbeef"}
    matched, reason = _record_matches_link(record, link)
    assert not matched
    assert reason and "sha256" in reason


# ---------------------------------------------------------------------------
# _candidate_record_paths and _get_repodata_record
# ---------------------------------------------------------------------------


def test_candidate_record_paths_are_metadata_derived(tmp_path: Path):
    """Candidate paths are computed from LINK metadata, never globbed."""
    candidates = _candidate_record_paths(
        tmp_path, "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert candidates == [
        tmp_path
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json",
        tmp_path
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json",
    ]


def test_get_repodata_record_legacy_layout(tmp_path: Path):
    """Pre-2.6 mamba and conda still use the flat ``<pkgs>/<dist>/info`` layout."""
    flat = tmp_path / "flat"
    _write_record(
        flat / "foo-1.0.0-bld" / "info" / "repodata_record.json",
        name="foo",
        version="1.0.0",
        subdir="linux-64",
    )
    record = _get_repodata_record(
        [flat],
        "foo-1.0.0-bld",
        {"name": "foo", "version": "1.0.0", "platform": "linux-64"},
    )
    assert record == {"name": "foo", "version": "1.0.0", "subdir": "linux-64"}


def test_get_repodata_record_hierarchical_layout(tmp_path: Path):
    """Mamba 2.6.0 hierarchical layout: pkgs/<channel-url>/<platform>/<dist>/info."""
    hier = tmp_path / "hier"
    _write_record(
        hier
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json",
        name="libzlib",
        version="1.3.2",
        subdir="linux-64",
        sha256=_MAMBA_26_LINK_ACTION["sha256"],
        url=_MAMBA_26_LINK_ACTION["url"],
    )
    record = _get_repodata_record(
        [hier], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert record is not None
    assert record["url"] == _MAMBA_26_LINK_ACTION["url"]


def test_get_repodata_record_rejects_cross_channel_collision(tmp_path: Path):
    """A different package with the same dist_name in a different channel must
    NOT be returned. Mamba 2.6.0's hierarchy exists precisely to disambiguate
    such collisions; conda-lock must not silently pick the wrong record."""
    pkgs = tmp_path / "pkgs"
    _write_record(
        pkgs
        / "https/repo.example.com/private/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json",
        name="libzlib",
        version="9.9.9",
        subdir="linux-64",
        sha256="deadbeef",
        url="https://repo.example.com/private/linux-64/libzlib-1.3.2-h25fd6f3_2.conda",
    )
    record = _get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert record is None


def test_get_repodata_record_logs_specific_reason_at_debug(tmp_path, caplog):
    """A wrong-hash impostor must log an identity-mismatch reason, not a
    generic 'not found'. Prevents the next debugging session from being
    blind to whether the record was missing or rejected."""
    pkgs = tmp_path / "pkgs"
    impostor = (
        pkgs
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json"
    )
    _write_record(
        impostor,
        name="libzlib",
        version="1.3.2",
        subdir="linux-64",
        sha256="deadbeef",
        url=_MAMBA_26_LINK_ACTION["url"],
    )
    with caplog.at_level("DEBUG", logger="conda_lock.conda_solver"):
        record = _get_repodata_record(
            [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
        )
    assert record is None
    debug_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "identity mismatch" in debug_text
    assert "sha256" in debug_text


def test_get_repodata_record_emits_only_one_warning_after_retries(
    tmp_path, caplog
):
    """One missing package previously produced 11 nearly-identical
    warnings (10 retries + final). Operators don't need that volume.
    Per-retry messages live at DEBUG; only the final give-up is WARNING."""
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    with caplog.at_level("DEBUG", logger="conda_lock.conda_solver"):
        record = _get_repodata_record(
            [pkgs], "missing-1.0.0-bld", _MAMBA_26_LINK_ACTION
        )
    assert record is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Giving up" in warnings[0].getMessage()
    # The retry chatter is still recoverable via DEBUG.
    debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert any("Retrying" in r.getMessage() for r in debugs)


def test_get_repodata_record_warning_prefers_rejected_over_missing(
    tmp_path, caplog
):
    """When one candidate path was found-and-rejected and another was
    missing, the final WARNING must report the rejection, not the
    missing-file. The rejection is the actionable signal; the missing
    flat-fallback is trivia. Previously the warning showed whichever
    happened last, which was usually the trivia."""
    pkgs = tmp_path / "pkgs"
    # Hierarchical path exists with a wrong-sha256 record.
    impostor = (
        pkgs
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json"
    )
    _write_record(
        impostor,
        name="libzlib",
        version="1.3.2",
        subdir="linux-64",
        sha256="deadbeef",
        url=_MAMBA_26_LINK_ACTION["url"],
    )
    # Flat fallback path is intentionally absent (the trivia).
    with caplog.at_level("WARNING", logger="conda_lock.conda_solver"):
        record = _get_repodata_record(
            [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
        )
    assert record is None
    final_warning = caplog.records[-1].getMessage()
    assert "identity mismatch" in final_warning
    assert "sha256" in final_warning
    assert "file not found" not in final_warning


# ---------------------------------------------------------------------------
# _reconstruct_fetch_actions integration
# ---------------------------------------------------------------------------


def test_reconstruct_fetch_actions_synthesizes_from_link(monkeypatch):
    """When LINK contains all FETCH fields (mamba 2.6.0), no disk access is
    needed and ``_get_pkgs_dirs`` must not be invoked."""

    def boom(**_kwargs):
        raise AssertionError("_get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.conda_solver._get_pkgs_dirs", boom)

    dryrun = {
        "actions": {
            "LINK": [_MAMBA_26_LINK_ACTION],
            "FETCH": [],
        }
    }
    result = _reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
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
        raise AssertionError("_get_pkgs_dirs should not be called")

    monkeypatch.setattr("conda_lock.conda_solver._get_pkgs_dirs", boom)

    fixture = (
        TESTS_DIR
        / "test-mamba-fixtures"
        / "dryrun-mamba-2.6.0-linux-64-zlib.json"
    )
    dryrun = json.loads(fixture.read_text())
    assert len(dryrun["actions"]["LINK"]) >= 1
    assert dryrun["actions"].get("FETCH", []) == []
    result = _reconstruct_fetch_actions("/dummy", "linux-64", dryrun)
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
    so this exercises ``_get_pkgs_dirs`` -> ``_candidate_record_paths`` ->
    file open -> ``_record_matches_link`` against a real cache directory
    laid out the way mamba 2.6.0 actually writes it.
    """
    fixture = (
        TESTS_DIR
        / "test-mamba-fixtures"
        / "dryrun-mamba-2.6.0-linux-64-zlib.json"
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
        # No `depends`, no `timestamp` -> _link_action_as_fetch returns None.
        "url": real_link["url"],
    }
    dryrun = {"actions": {"LINK": [sparse_link], "FETCH": []}}

    monkeypatch.setattr(
        "conda_lock.conda_solver._get_pkgs_dirs",
        lambda **_kwargs: [pkgs_dir],
    )

    result = _reconstruct_fetch_actions("/dummy", real_link["subdir"], dryrun)
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
        _reconstruct_fetch_actions("/dummy", real_link["subdir"], dryrun)
