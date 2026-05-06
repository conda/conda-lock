"""Local package-cache I/O and corruption detection.

This module owns the question "what does the on-disk cache say about a
given LINK action?" -- nothing else. URL normalization (libmamba
parity), candidate-path derivation across the legacy flat layout and
the mamba 2.6.0 hierarchical layout, identity validation,
recognition of the mamba 2.1.1-2.3.3 ``repodata_record.json``
stub-record corruption signature, and ``info/index.json``-based
healing of those stubs all live here.

Strict layering: this module imports from ``models`` only -- never
from ``conda_lock.conda_solver`` or ``conda_lock.solver.dry_run``.
Subprocess probing of the conda CLI (``get_pkgs_dirs``) lives in
``conda_lock.invoke_conda`` because it is a CLI concern, not a
cache-record concern. User-facing policy (warn-vs-fail) lives in
the orchestration layer (``conda_lock.solver.dry_run``,
``conda_lock.conda_solver``); this layer only returns narrow facts.
"""

import json
import logging
import pathlib
import re
import time

from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from conda_lock.models.dry_run_install import FetchAction, LinkAction


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepodataLookup:
    """Outcome of looking up ``repodata_record.json`` for one LINK action.

    The cache layer reports facts; the orchestration layer
    (``conda_lock.solver.dry_run``) maps each ``outcome`` to a
    user-facing warning. Mutually exclusive states:

    - ``"found"``: the cache had a clean record matching the LINK.
      ``record`` is the parsed FETCH-shaped dict.
    - ``"healed"``: the cache had a mamba 2.1.1-2.3.3 stub record;
      we recovered ``depends`` from the sibling ``info/index.json``
      and the resulting record matches the LINK. ``record`` is the
      healed dict; ``healed_from`` points at the corrupt record's
      path so the orchestration WARNING can name it.
    - ``"unhealable_corrupt"``: the cache had a stub record that
      cleared a two-stage identity gate -- (1) no contradictions on
      ``name`` / ``version`` / ``subdir`` / ``fn`` / ``md5`` /
      ``sha256`` / ``url`` (``record_matches_link``); (2) at least
      one strong *artifact identity* field
      (``url`` / ``md5`` / ``sha256`` / ``fn``) matched the LINK
      *positively* (``_stub_has_strong_identity_match``) -- but
      ``info/index.json`` was missing or unreadable, so we could
      not heal. Both gates are required before this outcome is
      assigned, so:

      * an impostor stub at a flat-fallback path (cross-channel
        ``dist_name`` collision) fails stage 1 and falls through to
        ``"not_found"``;
      * a stub that shares only ``name`` / ``version`` with the LINK
        (and possibly ``subdir``) but exposes no strong artifact
        field clears stage 1 by default but fails stage 2, also
        falling through to ``"not_found"``.

      The strong-identity precondition matters because
      ``record_matches_link`` skips fields when one side is empty,
      so its "True" return means "no contradictions," not "positive
      identity proven." ``subdir`` is deliberately not in the strong
      set: it narrows platform, not artifact. The
      ``unhealable_corrupt`` claim is operator-actionable
      (``mamba clean -a``) and must not be based on
      contradictions-not-found alone. ``record`` is ``None``;
      ``reason`` carries the diagnostic.
    - ``"not_found"``: no candidate file existed in any
      ``pkgs_dir``, or every found candidate failed identity checks
      against the LINK. ``record`` is ``None``; ``reason`` carries
      the most actionable diagnostic (rejected identity beats
      missing-file).
    """

    record: FetchAction | None
    outcome: Literal["found", "healed", "unhealable_corrupt", "not_found"]
    reason: str | None = None
    healed_from: pathlib.Path | None = None


# libmamba's token regex (`/t/([a-zA-Z0-9-_]{0,2}[a-zA-Z0-9-]*)`) matches
# `/t/<token>` with no requirement on a trailing path -- the URL may end
# right after the token. We accept the same character class without
# requiring a trailing slash.
_TOKEN_PATH_RE = re.compile(r"/t/[a-zA-Z0-9_-]*")


def libmamba_strip_url_secrets(url: str) -> str:
    """Strip credentials and conda auth tokens from a URL.

    This is a *libmamba-compat helper*, not a general-purpose URL
    sanitizer. The callers (cache path derivation and cache record
    URL comparison) need bit-for-bit parity with how libmamba 2.6.0
    cleans URLs, including the deliberately overbroad ``/t/<chars>``
    handling pinned in tests. Don't reuse this for security-sensitive
    URL scrubbing without re-reading what libmamba's
    ``remove_secrets_and_login_credentials`` actually does.

    Covers the cases that conda-lock encounters for package URLs:

    - ``scheme://user:pass@host/...`` -> userinfo dropped
    - ``host/t/<token>`` and ``host/t/<token>/path`` -> token segment removed
    - ``user:pass@host/...`` (no scheme) -> userinfo dropped
    """
    if "://" in url:
        parsed = urlsplit(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        cleaned_path = _TOKEN_PATH_RE.sub("", parsed.path)
        # `cast` is required for ty (its overload resolution on
        # ``urlunsplit`` keeps picking the bytes return path even with a
        # str-typed component tuple). mypy correctly infers ``str`` here
        # and would warn ``redundant-cast``, so we silence that one.
        return cast(  # type: ignore[redundant-cast]
            str,
            urlunsplit(
                (parsed.scheme, netloc, cleaned_path, parsed.query, parsed.fragment)
            ),
        )
    # Scheme-less URL: ``urlsplit`` parks everything in ``path``, so
    # handle userinfo and token explicitly. This mirrors libmamba's
    # explicit no-scheme tests in test_cpp.cpp.
    at_pos = url.find("@")
    slash_pos = url.find("/")
    if at_pos != -1 and (slash_pos == -1 or at_pos < slash_pos):
        url = url[at_pos + 1 :]
    return _TOKEN_PATH_RE.sub("", url)


def _normalize_url_for_cache_path(url: str) -> str:
    """Apply mamba 2.6.0's URL normalization for package cache paths.

    Strips credentials/tokens (matching libmamba's
    ``remove_secrets_and_login_credentials``), then mirrors
    ``package_cache_folder_relative_path``: scheme separators ``://``
    become ``/`` and remaining ``:`` / ``\\`` are replaced with ``_``.
    Path separators are preserved.
    """
    cleaned = libmamba_strip_url_secrets(url)
    return cleaned.replace("://", "/").replace(":", "_").replace("\\", "_")


def normalize_url_for_compare(url: str) -> str:
    """Mirror libmamba's ``compare_cleaned_url`` semantics.

    libmamba parses both URLs as ``CondaURL``, forces the scheme to
    ``https``, removes credentials, and compares the strings with their
    trailing slashes stripped. Two URLs that differ only by
    ``http``/``https``, by trailing slash, or by carrying credentials
    must still be treated as equal. We don't have ``CondaURL`` here, but
    a credential-stripped + scheme-normalized + slash-trimmed compare
    matches the cases that occur in practice for conda packages.
    """
    cleaned = libmamba_strip_url_secrets(url)
    parsed = urlsplit(cleaned)
    if parsed.scheme:
        cleaned = urlunsplit(parsed._replace(scheme="https"))
    return cleaned.rstrip("/")


def hierarchical_cache_subpath(link_action: LinkAction) -> pathlib.Path | None:
    """Return ``<normalized base url>/<subdir>`` for the mamba 2.6.0 layout.

    Prefers the LINK action's ``url`` (stripping the filename), falling back
    to ``base_url`` + ``platform``. Returns ``None`` when neither is usable.
    """
    url = link_action.get("url")
    platform = link_action.get("platform") or link_action.get("subdir") or ""
    directory: str | None = None
    if url and "/" in url:
        directory = url.rsplit("/", 1)[0]
    elif link_action.get("base_url") and platform:
        base = link_action["base_url"].rstrip("/")
        suffix = f"/{platform}"
        if base.endswith(suffix):
            base = base[: -len(suffix)]
        directory = f"{base}/{platform}"
    if directory is None:
        return None
    return pathlib.Path(_normalize_url_for_cache_path(directory))


def _link_action_explicit_or_derived_url(link_action: LinkAction) -> str | None:
    """Best-effort URL for the linked package, with explicit-vs-derived
    intent baked into the name.

    Returns the LINK's explicit ``url`` (from mamba 2.6.0+ or any solver
    that emits the full repodata in LINK) if present. Otherwise derives
    one from ``base_url``/``platform``/``fn`` -- this is the older-conda
    sparse-LINK case, where the URL is reconstructed from disjoint
    fields and is therefore weaker evidence than an explicit value.
    Both paths are still useful for ``record_matches_link`` to validate
    against a record on disk; future maintainers should treat a derived
    URL as a heuristic, not gospel.
    """
    url = link_action.get("url")
    if url:
        return url
    base_url = link_action.get("base_url")
    fn = link_action.get("fn")
    if not base_url or not fn:
        return None
    base = base_url.rstrip("/")
    platform = link_action.get("platform") or link_action.get("subdir")
    if platform:
        suffix = f"/{platform}"
        if not base.endswith(suffix):
            base = f"{base}{suffix}"
    return f"{base}/{fn}"


def record_matches_link(
    record: FetchAction, link_action: LinkAction
) -> tuple[bool, str | None]:
    """Confirm a ``repodata_record.json`` corresponds to the LINK we're looking up.

    Mamba 2.6.0's hierarchy exists precisely to disambiguate same-named
    distributions across channels, so a basename match in the cache is not
    proof of identity. We validate ``name`` and ``version`` positively, and
    cross-check ``subdir`` / ``fn`` / ``md5`` / ``sha256`` whenever both
    sides expose them. URL vs channel resolves like this:

    - If both sides expose a URL (LINK's explicit ``url`` or one derived
      from ``base_url``+``fn``), compare with ``compare_cleaned_url``
      semantics. We do NOT additionally enforce channel-string equality
      here, so mirrored channels whose channel string spelling differs
      (``"conda-forge"`` vs the canonical URL) but whose package URLs
      match are accepted -- matching libmamba's precedence.
    - Otherwise fall back to a channel-string compare so a sparse
      LINK without a URL still gets validated past name/version
      whenever both sides carry channel.

    Returns ``(matched, reason_if_rejected)``.
    """
    if record.get("name") != link_action.get("name"):
        return (
            False,
            f"name mismatch: record={record.get('name')!r} link={link_action.get('name')!r}",
        )
    if record.get("version") != link_action.get("version"):
        return (
            False,
            f"version mismatch: record={record.get('version')!r} link={link_action.get('version')!r}",
        )
    record_subdir = record.get("subdir")
    link_subdir = link_action.get("platform") or link_action.get("subdir")
    if record_subdir and link_subdir and record_subdir != link_subdir:
        return False, f"subdir mismatch: record={record_subdir!r} link={link_subdir!r}"
    for field in ("fn", "md5", "sha256"):
        link_val = link_action.get(field)
        record_val = record.get(field)
        if link_val and record_val and link_val != record_val:
            return False, f"{field} mismatch"
    record_url = record.get("url")
    link_url = _link_action_explicit_or_derived_url(link_action)
    url_compared = False
    if record_url and link_url:
        if normalize_url_for_compare(link_url) != normalize_url_for_compare(record_url):
            return False, f"url mismatch: record={record_url!r} link={link_url!r}"
        url_compared = True
    if not url_compared:
        link_channel = link_action.get("channel")
        record_channel = record.get("channel")
        if link_channel and record_channel and link_channel != record_channel:
            return (
                False,
                f"channel mismatch: record={record_channel!r} link={link_channel!r}",
            )
    return True, None


def candidate_record_paths(
    pkgs_dir: pathlib.Path,
    dist_name: str,
    link_action: LinkAction,
) -> list[pathlib.Path]:
    """Candidate ``repodata_record.json`` locations, in priority order.

    Conda and pre-2.6 mamba use a flat layout (``<pkgs_dir>/<dist_name>/...``).
    Mamba/micromamba 2.6.0 nests packages under the channel and subdir
    derived from the package URL (see mamba-org/mamba#4163). We compute the
    expected hierarchical path from the LINK metadata rather than walking the
    cache, then fall back to the legacy flat path.
    """
    candidates: list[pathlib.Path] = []
    sub = hierarchical_cache_subpath(link_action)
    if sub is not None:
        candidates.append(pkgs_dir / sub / dist_name / "info" / "repodata_record.json")
    candidates.append(pkgs_dir / dist_name / "info" / "repodata_record.json")
    return candidates


def is_mamba_2_1_to_2_3_stub_record(record: dict[str, Any]) -> bool:
    """Detect the ``repodata_record.json`` corruption signature from
    mamba/micromamba 2.1.1-2.3.3.

    Those versions wrote URL-stub metadata directly to ``repodata_record.json``
    instead of merging with ``info/index.json`` (mamba-org/mamba#4052,
    fixed in mamba-org/mamba#4110 / mamba 2.6.0). The combination of
    ``timestamp == 0`` and an empty ``license`` is the marker -- it never
    appears in legitimately-written records. Empty ``depends`` is the
    most damaging downstream symptom (it breaks ``apply_categories`` in
    conda-lock and silently drops packages from the lockfile).
    """
    if record.get("timestamp") != 0:
        return False
    if record.get("license", ""):
        return False
    # Either an empty ``depends`` or a missing ``sha256`` corroborates the
    # signature. The combination above is already specific enough that we
    # accept either.
    if record.get("depends") in (None, []):
        return True
    if not record.get("sha256"):
        return True
    return False


# Artifact identity fields strong enough to attribute a corrupt
# stub record to a specific LINK. Each value here uniquely (or
# near-uniquely) identifies a single conda package artifact:
#
#   * ``url`` -- channel + subdir + filename, the most precise.
#   * ``md5`` and ``sha256`` -- content-addressable hashes.
#   * ``fn`` -- name-version-build filename, unique within a channel
#     and subdir; combined with the LINK's name+version positive
#     match, it is enough to attribute the artifact.
#
# ``subdir`` is intentionally NOT here. It narrows platform, not
# artifact: ``linux-64`` matches every Linux package in the
# universe. ``record_matches_link`` already validates ``subdir`` as
# a *contradiction* check (``linux-64`` vs ``osx-arm64`` rejects),
# but using it as positive identity proof would let a stub with
# matching name + version + ``subdir=linux-64`` claim our package's
# cache is corrupt -- the exact "no contradictions" overreach we
# are trying to avoid.
_STRONG_IDENTITY_FIELDS: tuple[str, ...] = ("url", "md5", "sha256", "fn")


def _stub_has_strong_identity_match(
    record: FetchAction, link_action: LinkAction
) -> bool:
    """Return True iff at least one strong artifact identity field
    is populated on both sides and matches positively.

    ``record_matches_link`` returns ``True`` when nothing contradicts;
    that is not enough evidence to classify a corrupt stub as
    "unhealable corruption affecting this package". The mamba
    2.1.1-2.3.3 bug zeroed only ``depends`` / ``license`` /
    ``timestamp``, so a real corrupt stub for our package retains
    every cache identity field (url, md5, sha256, fn) and easily
    clears this bar. A stub that's missing all of those is an older
    or sparser record that we cannot positively attribute to our
    LINK -- treat it as ``not_found`` rather than overclaiming
    corruption. ``subdir`` is deliberately excluded; see the
    comment on ``_STRONG_IDENTITY_FIELDS``.
    """
    for field in _STRONG_IDENTITY_FIELDS:
        # ``FetchAction`` and ``LinkAction`` are TypedDicts, so
        # ``.get(field)`` with a runtime string yields ``object``;
        # cast to ``str | None`` since every entry in
        # ``_STRONG_IDENTITY_FIELDS`` is a string-typed key.
        record_val = cast("str | None", record.get(field))
        if not record_val:
            continue
        if field == "url":
            link_url = _link_action_explicit_or_derived_url(link_action)
            if link_url and normalize_url_for_compare(record_val) == (
                normalize_url_for_compare(link_url)
            ):
                return True
        else:
            link_val = cast("str | None", link_action.get(field))
            if link_val and record_val == link_val:
                return True
    return False


def heal_corrupt_record(
    record: dict[str, Any], record_path: pathlib.Path
) -> dict[str, Any] | None:
    """Repair a mamba 2.1.1-2.3.3 corrupt ``repodata_record.json``.

    Overlays the corrupt record with ``info/index.json`` from the same
    package directory. ``info/index.json`` is extracted directly from the
    package tarball at install time and is never affected by the bug, so
    it carries the canonical ``depends``, ``constrains``, ``timestamp``,
    ``license``, ``build_number``, etc. Cache-derived fields (``url``,
    ``channel``, ``fn``, ``md5``, ``sha256``, ``size``) are kept from the
    record when non-empty.

    Returns the healed record, or ``None`` if ``info/index.json`` is
    missing or unreadable.
    """
    index_path = record_path.parent / "index.json"
    if not index_path.is_file():
        return None
    try:
        with open(index_path) as f:
            index_data: dict[str, Any] = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    healed = dict(index_data)
    # Overlay only non-stub values from the cache record. This recovers
    # the ``url`` / ``channel`` / ``md5`` / ``sha256`` that ``index.json``
    # doesn't carry, but skips fields the bug zeroed out.
    for key, value in record.items():
        if value in ("", 0, [], None):
            continue
        healed[key] = value
    return healed


def get_repodata_record(
    pkgs_dirs: list[pathlib.Path],
    dist_name: str,
    link_action: LinkAction,
) -> RepodataLookup:
    """Look up ``repodata_record.json`` for one LINK action in the cache.

    Returns a ``RepodataLookup`` with one of four ``outcome`` states.
    The orchestration layer (``conda_lock.solver.dry_run``) maps
    each outcome to a user-facing warning; this function only emits
    DEBUG-level diagnostics.

    On rare occasion during CI tests, conda fails to find a package
    in the package cache, perhaps because the package is still being
    processed; waiting 0.1 seconds seems to solve the issue. Here we
    allow up to a full second to elapse before giving up.

    Records matching the mamba 2.1.1-2.3.3 corruption signature
    (``timestamp == 0`` + empty ``license``) are healed from
    ``info/index.json`` rather than skipped, so existing corrupt
    caches don't keep producing broken lockfiles after the user
    upgrades. The "healed" outcome surfaces this fact to the
    orchestration layer so the appropriate WARNING with operator
    remediation text can be emitted there.

    Distinct failure modes (missing file, JSON corruption, identity
    mismatch, unhealable corruption) are tracked through the retry
    loop so the final ``RepodataLookup.reason`` is the most
    actionable diagnostic. "Rejected" beats "missing": if we found
    a candidate and rejected it for, say, sha256 mismatch, that's
    the signal worth surfacing -- not the trivia that the legacy
    flat fallback path didn't exist.
    """
    NUM_RETRIES = 10
    last_rejected: str | None = None
    last_missing: str | None = None
    last_unhealable: str | None = None
    for retry in range(1, NUM_RETRIES + 1):
        for pkgs_dir in pkgs_dirs:
            for candidate in candidate_record_paths(pkgs_dir, dist_name, link_action):
                if not candidate.is_file():
                    last_missing = f"file not found: {candidate}"
                    logger.debug(last_missing)
                    continue
                try:
                    with open(candidate) as f:
                        record: FetchAction = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    last_rejected = f"failed to read {candidate}: {exc}"
                    logger.debug(last_rejected)
                    continue
                healed_from: pathlib.Path | None = None
                if is_mamba_2_1_to_2_3_stub_record(cast(dict, record)):
                    # Two-stage identity gate before classifying this
                    # stub as our package's unhealable corruption.
                    #
                    # Stage 1: ``record_matches_link`` rejects on any
                    # *contradiction* in name/version/subdir/fn/md5/
                    # sha256/url. The legacy flat layout
                    # (``<pkgs>/<dist_name>/info/...``) is keyed on
                    # dist_name alone, so a stub there can be from a
                    # different package that happens to share the
                    # dist name (cross-channel collision, stale
                    # extracted dir). Reject those.
                    matched, reason = record_matches_link(record, link_action)
                    if not matched:
                        last_rejected = (
                            f"corrupt-looking candidate at {candidate} "
                            f"rejected: {reason}"
                        )
                        logger.debug(last_rejected)
                        continue
                    # Stage 2: ``record_matches_link`` is satisfied by
                    # "no contradictions" and *skips* artifact fields
                    # that one side leaves empty. That is fine for
                    # general matching, but for the
                    # ``unhealable_corrupt`` claim we need positive
                    # proof that the corruption applies to *this*
                    # package, not just an older/sparser record that
                    # happens to share the dist name (or a platform).
                    # Require at least one strong artifact identity
                    # field (url/md5/sha256/fn) to match positively.
                    # ``subdir`` is excluded -- it narrows platform,
                    # not artifact, and a matching ``linux-64``
                    # alone is the kind of "no contradictions"
                    # overreach we are trying to avoid. The mamba
                    # 2.1.1-2.3.3 bug zeroed only ``depends`` /
                    # ``license`` / ``timestamp``, so a real corrupt
                    # stub for our package keeps every artifact
                    # identity field and clears this bar trivially.
                    if not _stub_has_strong_identity_match(record, link_action):
                        last_rejected = (
                            f"corrupt-looking candidate at {candidate} "
                            f"shares name/version with the LINK but has "
                            f"no strong artifact identity "
                            f"(url/md5/sha256/fn) to confirm it is "
                            f"the same package -- not enough evidence "
                            f"to claim unhealable corruption"
                        )
                        logger.debug(last_rejected)
                        continue
                    healed = heal_corrupt_record(cast(dict, record), candidate)
                    if healed is None:
                        last_unhealable = (
                            f"corrupt record at {candidate} (mamba 2.1.1-2.3.3 "
                            f"signature) matched the LINK but info/index.json "
                            f"is missing -- cannot heal"
                        )
                        logger.debug(last_unhealable)
                        continue
                    record = cast(FetchAction, healed)
                    healed_from = candidate
                matched, reason = record_matches_link(record, link_action)
                if matched:
                    return RepodataLookup(
                        record=record,
                        outcome="healed" if healed_from else "found",
                        healed_from=healed_from,
                    )
                last_rejected = f"identity mismatch at {candidate}: {reason}"
                logger.debug(last_rejected)
        # Per-retry summary stays at DEBUG so a single missing package
        # doesn't drown operator output in 11 nearly-identical lines.
        # The orchestration layer logs a single WARNING after the
        # retry loop returns.
        final_reason = last_rejected or last_unhealable or last_missing
        logger.debug(
            f"Failed to find repodata_record.json for {dist_name} "
            f"(last reason: {final_reason}). "
            f"Retrying in 0.1 seconds ({retry}/{NUM_RETRIES})"
        )
        time.sleep(0.1)
    # Outcome priority: ``unhealable_corrupt`` wins over
    # ``not_found``. ``last_unhealable`` is set only when an
    # identity-proven stub record (cleared both gates above) failed
    # to heal because ``info/index.json`` was missing -- which is
    # the operator-actionable fact (``mamba clean -a`` recovers the
    # whole cache). A later candidate's identity rejection or
    # missing file is noise from the retry fan-out and must not
    # demote that signal.
    if last_unhealable:
        return RepodataLookup(
            record=None, outcome="unhealable_corrupt", reason=last_unhealable
        )
    return RepodataLookup(
        record=None,
        outcome="not_found",
        reason=last_rejected or last_missing,
    )
