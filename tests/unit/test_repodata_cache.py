"""Unit tests for ``conda_lock.solver.repodata_cache``.

Cache-side concerns: URL normalization (libmamba parity),
hierarchical and legacy candidate-path derivation, identity checks
between cache records and LINK actions, mamba 2.1.1-2.3.3 stub-record
detection, and ``info/index.json``-based healing of those stubs.

The dict literals model real mamba/conda JSON output, which is
dynamically typed at the boundary -- pretending they were TypedDicts
at every call site would be a wall of casts without catching real
bugs. The relevant arg-type checks are disabled file-wide.
"""

# mypy: disable-error-code="arg-type,comparison-overlap"

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from conda_lock.solver.repodata_cache import (
    candidate_record_paths,
    get_repodata_record,
    heal_corrupt_record,
    hierarchical_cache_subpath,
    is_mamba_2_1_to_2_3_stub_record,
    libmamba_strip_url_secrets,
    normalize_url_for_compare,
    record_matches_link,
)
from tests.support.fixtures import MAMBA_26_LINK_ACTION as _MAMBA_26_LINK_ACTION


def _matched(record: dict, link: dict) -> bool:
    matched, _reason = record_matches_link(record, link)
    return matched


def _write_record(path: Path, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields))


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_libmamba_strip_url_secrets_handles_no_scheme_credentials():
    """libmamba removes ``user:pass@`` and ``/t/<token>`` even on URLs
    without a scheme (see ``test_cpp.cpp`` ``URLs without scheme``)."""
    assert (
        libmamba_strip_url_secrets("root:secretpassword@myweb.com/test.repo")
        == "myweb.com/test.repo"
    )
    assert (
        libmamba_strip_url_secrets("myweb.com/t/my-12345-token/test.repo")
        == "myweb.com/test.repo"
    )
    # Don't get fooled by '@' that appears after the path separator.
    assert (
        libmamba_strip_url_secrets("myweb.com/path/with@in-it/foo")
        == "myweb.com/path/with@in-it/foo"
    )


def test_libmamba_strip_url_secrets_token_without_trailing_slash():
    """libmamba's token regex matches ``/t/<token>`` whether or not there's
    a trailing path component; the previous regex required a trailing
    slash, leaving terminal-token URLs untouched."""
    assert (
        libmamba_strip_url_secrets("https://repo.com/t/my-token") == "https://repo.com"
    )
    assert (
        libmamba_strip_url_secrets("https://repo.com/t/my-token/path")
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
        libmamba_strip_url_secrets("https://repo.com/not/t/pkg.conda")
        == "https://repo.com/not.conda"
    )


def test_normalize_url_for_compare_force_https_strip_slash():
    """The normalized form drops scheme differences and trailing slashes."""
    assert normalize_url_for_compare(
        "http://conda.example.com/c/linux-64/foo.conda"
    ) == normalize_url_for_compare("https://conda.example.com/c/linux-64/foo.conda/")
    assert normalize_url_for_compare(
        "https://user:pw@conda.example.com/c/linux-64/foo.conda"
    ) == normalize_url_for_compare("https://conda.example.com/c/linux-64/foo.conda")


# ---------------------------------------------------------------------------
# hierarchical_cache_subpath
# ---------------------------------------------------------------------------


def test_hierarchical_cache_subpath_from_url():
    """The cache subpath is derived from the package URL, not a glob."""
    sub = hierarchical_cache_subpath(_MAMBA_26_LINK_ACTION)
    assert sub == Path("https/conda.anaconda.org/conda-forge/linux-64")


def test_hierarchical_cache_subpath_from_base_url_and_platform():
    """Falls back to base_url + platform when the URL is absent."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "platform": "linux-64",
    }
    assert hierarchical_cache_subpath(sparse) == Path(
        "https/conda.anaconda.org/conda-forge/linux-64"
    )


def test_hierarchical_cache_subpath_strips_duplicated_platform_suffix():
    """``base_url`` may already include the subdir; don't double it up."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge/linux-64",
        "platform": "linux-64",
    }
    assert hierarchical_cache_subpath(sparse) == Path(
        "https/conda.anaconda.org/conda-forge/linux-64"
    )


def test_hierarchical_cache_subpath_normalizes_port_separator():
    """Per mamba 2.6.0, a port's ``:`` is escaped to ``_``."""
    link = {"url": "http://localhost:8000/mychannel/noarch/foo-1.0-bld.conda"}
    assert hierarchical_cache_subpath(link) == Path(
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
    assert hierarchical_cache_subpath(with_userinfo) == Path(
        "https/conda.example.com/private/linux-64"
    )
    with_token = {
        "url": "https://conda.anaconda.org/t/aa-bbb-ccc/private/linux-64/foo.conda"
    }
    assert hierarchical_cache_subpath(with_token) == Path(
        "https/conda.anaconda.org/private/linux-64"
    )


# ---------------------------------------------------------------------------
# record_matches_link
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
    record = {
        **name_version,
        "url": url,
        "channel": "https://conda.anaconda.org/conda-forge",
    }
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
    matched, reason = record_matches_link(record, sparse_link_other_channel)
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
    matched, reason = record_matches_link(record, bad_link)
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
    matched, reason = record_matches_link(record, link)
    assert not matched
    assert reason and "sha256" in reason


# ---------------------------------------------------------------------------
# candidate_record_paths and get_repodata_record
# ---------------------------------------------------------------------------


def test_candidate_record_paths_are_metadata_derived(tmp_path: Path):
    """Candidate paths are computed from LINK metadata, never globbed."""
    candidates = candidate_record_paths(
        tmp_path, "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert candidates == [
        tmp_path
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
        / "repodata_record.json",
        tmp_path / "libzlib-1.3.2-h25fd6f3_2" / "info" / "repodata_record.json",
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
    lookup = get_repodata_record(
        [flat],
        "foo-1.0.0-bld",
        {"name": "foo", "version": "1.0.0", "platform": "linux-64"},
    )
    assert lookup.outcome == "found"
    assert lookup.record == {"name": "foo", "version": "1.0.0", "subdir": "linux-64"}


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
    lookup = get_repodata_record(
        [hier], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "found"
    assert lookup.record is not None
    assert lookup.record["url"] == _MAMBA_26_LINK_ACTION["url"]


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
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "not_found"
    assert lookup.record is None


def test_get_repodata_record_logs_specific_reason_at_debug(tmp_path, caplog):
    """A wrong-hash impostor must log an identity-mismatch reason at
    DEBUG, not a generic 'not found'. Prevents the next debugging
    session from being blind to whether the record was missing or
    rejected. The structured ``RepodataLookup.reason`` field carries
    the same diagnostic for orchestration consumers."""
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
    with caplog.at_level("DEBUG", logger="conda_lock.solver.repodata_cache"):
        lookup = get_repodata_record(
            [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
        )
    assert lookup.outcome == "not_found"
    assert lookup.record is None
    assert lookup.reason is not None
    assert "identity mismatch" in lookup.reason
    assert "sha256" in lookup.reason
    debug_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "identity mismatch" in debug_text
    assert "sha256" in debug_text


def test_get_repodata_record_emits_no_warnings_from_cache_layer(tmp_path, caplog):
    """The cache layer is silent at WARNING level by contract: it
    returns a structured ``RepodataLookup`` and the orchestration
    layer (``conda_lock.solver.dry_run``) decides what to log. This
    keeps policy out of the cache module. Per-retry chatter still
    lives at DEBUG so an operator can `pytest --log-cli-level=DEBUG`
    when investigating.
    """
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    with caplog.at_level("DEBUG", logger="conda_lock.solver.repodata_cache"):
        lookup = get_repodata_record([pkgs], "missing-1.0.0-bld", _MAMBA_26_LINK_ACTION)
    assert lookup.outcome == "not_found"
    assert lookup.record is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert any("Retrying" in r.getMessage() for r in debugs)


def test_get_repodata_record_reason_prefers_rejected_over_missing(tmp_path):
    """When one candidate path was found-and-rejected and another was
    missing, ``RepodataLookup.reason`` must surface the rejection,
    not the missing-file. The rejection is the actionable signal;
    the missing flat-fallback is trivia. Previously the surfaced
    reason was whichever fired last, which was usually the trivia.
    """
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
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "not_found"
    assert lookup.reason is not None
    assert "identity mismatch" in lookup.reason
    assert "sha256" in lookup.reason
    assert "file not found" not in lookup.reason


# --- 1. Corruption detection + heal -------------------------------------


def test_is_mamba_2x_corrupt_record_signature():
    """``timestamp == 0`` + empty ``license`` is the marker. We accept the
    signature whether ``depends`` is empty or ``sha256`` is missing -- the
    bug zeroed both in different release windows."""
    assert is_mamba_2_1_to_2_3_stub_record(
        {"timestamp": 0, "license": "", "depends": [], "sha256": "abc"}
    )
    assert is_mamba_2_1_to_2_3_stub_record(
        {"timestamp": 0, "license": "", "depends": ["x"], "sha256": ""}
    )
    # A fresh, healthy record from mamba 2.6.0 has neither a zero
    # timestamp nor an empty license; the signature must NOT trigger.
    assert not is_mamba_2_1_to_2_3_stub_record(
        {
            "timestamp": 1774072620,
            "license": "Zlib",
            "depends": [],
            "sha256": "abc",
        }
    )
    # Real legacy record without sha256 but with valid timestamp/license
    # also must not trigger.
    assert not is_mamba_2_1_to_2_3_stub_record(
        {"timestamp": 12345, "license": "MIT", "depends": ["x"]}
    )


def test_heal_corrupt_record_overlays_index_json(tmp_path: Path):
    """The healed record uses ``info/index.json`` for canonical metadata
    and keeps non-stub cache fields (url/md5/sha256) from the corrupt
    record."""
    info_dir = tmp_path / "pkgs/zlib-1.3.2-bld/info"
    info_dir.mkdir(parents=True)
    (info_dir / "index.json").write_text(
        json.dumps(
            {
                "name": "zlib",
                "version": "1.3.2",
                "build": "bld",
                "build_number": 2,
                "depends": ["__glibc >=2.17"],
                "license": "Zlib",
                "subdir": "linux-64",
                "timestamp": 1774072620,
            }
        )
    )
    record = {
        "name": "zlib",
        "version": "1.3.2",
        "depends": [],
        "license": "",
        "timestamp": 0,
        "sha256": "real-sha-from-cache",
        "md5": "real-md5-from-cache",
        "url": "https://conda.example.com/zlib-1.3.2-bld.conda",
        "channel": "conda-forge",
    }
    healed = heal_corrupt_record(record, info_dir / "repodata_record.json")
    assert healed is not None
    assert healed["depends"] == ["__glibc >=2.17"]
    assert healed["license"] == "Zlib"
    assert healed["timestamp"] == 1774072620
    # Non-stub cache fields preserved.
    assert healed["sha256"] == "real-sha-from-cache"
    assert healed["url"] == "https://conda.example.com/zlib-1.3.2-bld.conda"
    assert healed["channel"] == "conda-forge"


def test_heal_corrupt_record_returns_none_without_index_json(tmp_path: Path):
    """If ``info/index.json`` is missing the heal is impossible."""
    info_dir = tmp_path / "pkgs/zlib-1.3.2-bld/info"
    info_dir.mkdir(parents=True)
    record = {"name": "zlib", "depends": [], "license": "", "timestamp": 0}
    assert heal_corrupt_record(record, info_dir / "repodata_record.json") is None


def test_get_repodata_record_heals_corrupt_record_in_disk_fallback(
    tmp_path: Path,
):
    """End-to-end: a corrupt cache record is healed and returned with
    canonical depends/license/timestamp from ``info/index.json``. The
    cache layer surfaces the heal via ``RepodataLookup(outcome="healed",
    healed_from=<path>)``; the orchestration layer
    (``conda_lock.solver.dry_run``) is the one that translates that
    into a user-facing WARNING with ``mamba clean -a`` remediation
    text. See ``tests/unit/test_dry_run_actions.py`` for the
    boundary-translation tests."""
    pkgs = tmp_path / "pkgs"
    info_dir = (
        pkgs
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
    )
    info_dir.mkdir(parents=True)
    (info_dir / "index.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "build": "h25fd6f3_2",
                "depends": ["__glibc >=2.17,<3.0.a0"],
                "license": "Zlib",
                "subdir": "linux-64",
                "timestamp": 1774072609,
            }
        )
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
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "healed"
    assert lookup.record is not None
    assert lookup.healed_from is not None
    record_dict: dict[str, Any] = dict(lookup.record)
    assert record_dict["depends"] == ["__glibc >=2.17,<3.0.a0"]
    assert record_dict["license"] == "Zlib"
    assert record_dict["timestamp"] == 1774072609
    assert record_dict["url"] == _MAMBA_26_LINK_ACTION["url"]


def test_get_repodata_record_reports_unhealable_corrupt_record(tmp_path):
    """If ``info/index.json`` is absent next to an identity-matching
    corrupt record we can't heal it. The cache layer surfaces
    ``outcome="unhealable_corrupt"`` and the orchestration layer
    (``conda_lock.solver.dry_run``) is responsible for the WARNING
    with operator remediation text. Note: the stub keeps the cache
    metadata fields (name/version/url/md5/sha256) -- only
    ``depends`` / ``license`` / ``timestamp`` are zeroed by the bug
    -- so the identity precondition for ``unhealable_corrupt`` is
    satisfied."""
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
                "subdir": "linux-64",
                "url": _MAMBA_26_LINK_ACTION["url"],
                "md5": _MAMBA_26_LINK_ACTION["md5"],
                "sha256": _MAMBA_26_LINK_ACTION["sha256"],
                # The bug's signature: depends/license/timestamp zeroed.
                "depends": [],
                "license": "",
                "timestamp": 0,
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "unhealable_corrupt"
    assert lookup.record is None
    assert lookup.reason is not None
    assert "info/index.json" in lookup.reason


def test_get_repodata_record_unhealable_corrupt_beats_rejection(tmp_path):
    """Outcome priority: if any candidate carried the mamba 2.1.1-2.3.3
    stub-record signature and could not be healed, that fact wins
    over an identity rejection on another candidate. ``mamba clean -a``
    is the operator-facing fix for unhealable corruption; a sha256
    mismatch on a stale flat-fallback record is noise. Surfacing the
    rejection would let the actionable fact get silently demoted to
    ``not_found``.

    Layout: hierarchical candidate is a mamba 2.1.1-2.3.3 stub with
    no sibling ``info/index.json`` (so heal is impossible), and the
    legacy flat-fallback candidate exists but with a wrong sha256.
    Without the priority fix, ``not_found`` won because
    ``last_rejected`` was set after ``last_unhealable``.
    """
    pkgs = tmp_path / "pkgs"
    # Hierarchical: corrupt + unhealable. The stub preserves the
    # cache identity fields (the bug zeroed only depends / license /
    # timestamp), so it survives the identity precondition for
    # ``unhealable_corrupt``.
    hier_info = (
        pkgs
        / "https/conda.anaconda.org/conda-forge/linux-64"
        / "libzlib-1.3.2-h25fd6f3_2"
        / "info"
    )
    hier_info.mkdir(parents=True)
    (hier_info / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "subdir": "linux-64",
                "url": _MAMBA_26_LINK_ACTION["url"],
                "md5": _MAMBA_26_LINK_ACTION["md5"],
                "sha256": _MAMBA_26_LINK_ACTION["sha256"],
                "depends": [],
                "license": "",
                "timestamp": 0,
            }
        )
    )
    # Flat fallback: present but wrong sha256 -> identity-rejected.
    flat_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    flat_info.mkdir(parents=True)
    (flat_info / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "subdir": "linux-64",
                "sha256": "deadbeef",
                "url": _MAMBA_26_LINK_ACTION["url"],
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "unhealable_corrupt"
    assert lookup.record is None
    assert lookup.reason is not None
    assert "info/index.json" in lookup.reason


def test_get_repodata_record_unhealable_requires_identity_match(tmp_path):
    """``unhealable_corrupt`` is meaningful only if the corrupt stub
    record actually corresponds to the requested LINK. The legacy
    flat layout (``<pkgs>/<dist_name>/info/...``) is keyed on
    ``dist_name`` alone, so an impostor stub from a different
    package can sit at exactly the path we will inspect.

    Without the identity precondition, that impostor's corruption
    signature would hijack the outcome -- because
    ``unhealable_corrupt`` wins priority over ``not_found``, the
    operator would see "your cache is corrupt, run mamba clean -a"
    when the truth is "this isn't our package's record". The test
    pins the corrected behavior: a stub with mismatched ``name``
    falls through to ``not_found`` carrying the rejection reason,
    not a false-positive corruption claim.
    """
    pkgs = tmp_path / "pkgs"
    # Flat-fallback layout: dist_name happens to collide with the
    # one we're searching for, but the record inside is for a
    # different package and looks like a 2.1.1-2.3.3 stub.
    flat_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    flat_info.mkdir(parents=True)
    (flat_info / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "different-package",  # impostor identity
                "version": "9.9.9",
                "depends": [],
                "license": "",
                "timestamp": 0,
                "subdir": "linux-64",
                "url": "https://other.example.com/different-package-9.9.9-bld.conda",
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "not_found"
    assert lookup.record is None
    assert lookup.reason is not None
    # The reason must surface the identity rejection, not claim
    # corruption affecting our package.
    assert "rejected" in lookup.reason
    assert "name mismatch" in lookup.reason


def test_get_repodata_record_unhealable_requires_strong_identity_proof(tmp_path):
    """Identity gate stage 2: a stub that shares only ``name`` /
    ``version`` with the LINK and exposes no strong artifact
    identity field (``url`` / ``md5`` / ``sha256`` / ``fn``) must
    NOT be classified as ``unhealable_corrupt``. The bar is
    positive proof that the corruption is ours; "no contradictions"
    is not enough.
    """
    pkgs = tmp_path / "pkgs"
    flat_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    flat_info.mkdir(parents=True)
    (flat_info / "repodata_record.json").write_text(
        json.dumps(
            {
                # name + version match the LINK -- nothing contradicts.
                "name": "libzlib",
                "version": "1.3.2",
                # No url/md5/sha256/fn -- no positive identity proof.
                "depends": [],
                "license": "",
                "timestamp": 0,
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "not_found"
    assert lookup.record is None
    assert lookup.reason is not None
    assert "no strong artifact identity" in lookup.reason
    assert "not enough evidence" in lookup.reason


def test_get_repodata_record_unhealable_accepts_single_strong_field(tmp_path):
    """Counterpart to the strong-identity test: a single matching
    strong artifact field (here, ``sha256``) is sufficient to
    clear the ``unhealable_corrupt`` precondition. The bar is
    "at least one positive match," not "all fields match." A real
    corrupt stub keeps every cache field, but we don't want to
    require all of them -- some legacy records may omit ``url`` or
    ``md5`` while still carrying ``sha256``.
    """
    pkgs = tmp_path / "pkgs"
    flat_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    flat_info.mkdir(parents=True)
    (flat_info / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                # Only one strong identity field present -- but it
                # matches positively, which is enough.
                "sha256": _MAMBA_26_LINK_ACTION["sha256"],
                "depends": [],
                "license": "",
                "timestamp": 0,
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "unhealable_corrupt"
    assert lookup.record is None


def test_get_repodata_record_unhealable_rejects_subdir_only_match(tmp_path):
    """``subdir`` is platform metadata, not artifact identity. A
    corrupt stub with matching ``name`` / ``version`` / ``subdir``
    but no ``url`` / ``md5`` / ``sha256`` / ``fn`` must NOT clear
    the strong-identity gate. ``subdir=linux-64`` is shared by
    every Linux package in the universe; using it as positive
    identity proof would let any platform-matching stub claim
    unhealable corruption for our package -- the precise "no
    contradictions" overreach the gate exists to prevent.

    ``record_matches_link`` still validates ``subdir`` as a
    *contradiction* check (a stub with ``subdir=osx-arm64`` would
    be rejected at stage 1), so dropping ``subdir`` from the strong
    set keeps full safety on the negative side without granting
    spurious positive evidence.
    """
    pkgs = tmp_path / "pkgs"
    flat_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    flat_info.mkdir(parents=True)
    (flat_info / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                # Matches the LINK's subdir but adds nothing
                # artifact-specific. The stub corruption signature
                # fires (timestamp=0, license="", depends=[]).
                "subdir": "linux-64",
                "depends": [],
                "license": "",
                "timestamp": 0,
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "not_found"
    assert lookup.record is None
    assert lookup.reason is not None
    assert "no strong artifact identity" in lookup.reason
    assert "not enough evidence" in lookup.reason


def test_get_repodata_record_record_matches_link_still_rejects_subdir_contradiction(
    tmp_path,
):
    """Counterpart safety check: removing ``subdir`` from the
    strong-identity set must NOT weaken contradiction detection.
    A stub with ``subdir=osx-arm64`` against a ``linux-64`` LINK
    should still be rejected at stage 1 (``record_matches_link``)
    and never reach the strong-identity gate.
    """
    pkgs = tmp_path / "pkgs"
    flat_info = pkgs / "libzlib-1.3.2-h25fd6f3_2" / "info"
    flat_info.mkdir(parents=True)
    (flat_info / "repodata_record.json").write_text(
        json.dumps(
            {
                "name": "libzlib",
                "version": "1.3.2",
                "subdir": "osx-arm64",  # contradicts LINK's linux-64
                "url": _MAMBA_26_LINK_ACTION["url"],
                "md5": _MAMBA_26_LINK_ACTION["md5"],
                "sha256": _MAMBA_26_LINK_ACTION["sha256"],
                "depends": [],
                "license": "",
                "timestamp": 0,
            }
        )
    )
    lookup = get_repodata_record(
        [pkgs], "libzlib-1.3.2-h25fd6f3_2", _MAMBA_26_LINK_ACTION
    )
    assert lookup.outcome == "not_found"
    assert lookup.record is None
    assert lookup.reason is not None
    assert "subdir mismatch" in lookup.reason
    # Stage 1 short-circuited; the strong-identity diagnostic must
    # NOT appear (we never reached stage 2).
    assert "no strong artifact identity" not in lookup.reason
