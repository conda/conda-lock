"""Local package-cache I/O for the conda-lock dryrun pipeline.

This module owns the question "what does the on-disk cache say about a
given LINK action?" -- nothing else. URL normalization (libmamba
parity), candidate-path derivation across the legacy flat layout and
the mamba 2.6.0 hierarchical layout, identity validation between
cache records and LINK actions, and ``repodata_record.json``
lookup live here.

Strict layering: this module imports only from ``models``. URL
normalization is local to this module since it is a cache-path
concern; the same helpers may later be useful elsewhere, but the
boundary stays cache-cache for now.
"""

import json
import logging
import pathlib
import re
import time

from typing import cast
from urllib.parse import urlsplit, urlunsplit

from conda_lock.models.dry_run_install import FetchAction, LinkAction


logger = logging.getLogger(__name__)


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
    must still be treated as equal.
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
    - Otherwise fall back to a channel-string compare so a sparse LINK
      without a URL still gets validated past name/version whenever both
      sides carry channel.

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

    Conda and pre-2.6 mamba use a flat layout
    (``<pkgs_dir>/<dist_name>/...``). Mamba/micromamba 2.6.0 nests
    packages under the channel and subdir derived from the package URL
    (see mamba-org/mamba#4163). We compute the expected hierarchical
    path from the LINK metadata rather than walking the cache, then
    fall back to the legacy flat path.
    """
    candidates: list[pathlib.Path] = []
    sub = hierarchical_cache_subpath(link_action)
    if sub is not None:
        candidates.append(pkgs_dir / sub / dist_name / "info" / "repodata_record.json")
    candidates.append(pkgs_dir / dist_name / "info" / "repodata_record.json")
    return candidates


def get_repodata_record(
    pkgs_dirs: list[pathlib.Path],
    dist_name: str,
    link_action: LinkAction,
) -> FetchAction | None:
    """Look up ``repodata_record.json`` for one LINK action in the cache.

    Validates each found candidate against the LINK via
    ``record_matches_link`` so a same-dist record from a different
    channel (legacy flat layout collision) cannot be silently
    returned for the wrong package.

    On rare occasion during CI tests, conda fails to find a package
    in the package cache; waiting 0.1 seconds resolves it. Allow up
    to a full second to elapse before giving up. Distinct failure
    modes are logged at DEBUG so that a final ``not found`` doesn't
    bury whether we never saw the file or saw it and rejected it.
    """
    NUM_RETRIES = 10
    last_rejected: str | None = None
    last_missing: str | None = None
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
                matched, reason = record_matches_link(record, link_action)
                if matched:
                    return record
                last_rejected = f"identity mismatch at {candidate}: {reason}"
                logger.debug(last_rejected)
        final_reason = last_rejected or last_missing
        logger.debug(
            f"Failed to find repodata_record.json for {dist_name} "
            f"(last reason: {final_reason}). "
            f"Retrying in 0.1 seconds ({retry}/{NUM_RETRIES})"
        )
        time.sleep(0.1)
    final_reason = last_rejected or last_missing
    logger.warning(
        f"Failed to find repodata_record.json for {dist_name}. Giving up. "
        f"Last reason: {final_reason}"
    )
    return None
