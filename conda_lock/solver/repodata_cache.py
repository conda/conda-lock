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

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from conda_lock.models.dry_run_install import FetchAction


logger = logging.getLogger(__name__)


# libmamba's token regex (`/t/([a-zA-Z0-9-_]{0,2}[a-zA-Z0-9-]*)`) matches
# `/t/<token>` with no requirement on a trailing path -- the URL may end
# right after the token. We accept the same character class without
# requiring a trailing slash.
# https://github.com/mamba-org/mamba/blob/e0172cadfb4c286ff51b08c0314bdcf213707c0f/libmamba/src/core/util.cpp#L77
_TOKEN_PATH_RE = re.compile(r"/t/[a-zA-Z0-9_-]*")


def libmamba_strip_url_secrets(url: str) -> str:
    """Strip credentials and conda auth tokens from a URL.

    This is a *libmamba-compat helper*, not a general-purpose URL
    sanitizer. The callers (cache path derivation and cache record
    URL comparison) follow libmamba 2.6.0 for the package URL forms tested
    here, including its deliberately broad ``/t/<chars>`` handling. This
    does not reproduce every input accepted by its text sanitizer (for
    example, already-masked tokens). Do not use it for security-sensitive
    URL scrubbing. Upstream ``remove_secrets_and_login_credentials``:
    https://github.com/mamba-org/mamba/blob/e0172cadfb4c286ff51b08c0314bdcf213707c0f/libmamba/src/core/util.cpp#L1673

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
        return urlunsplit(
            (parsed.scheme, netloc, cleaned_path, parsed.query, parsed.fragment)
        )
    # Scheme-less URL: ``urlsplit`` parks everything in ``path``, so
    # handle userinfo and token explicitly. This mirrors libmamba's
    # explicit no-scheme tests in test_cpp.cpp.
    # https://github.com/mamba-org/mamba/blob/e0172cadfb4c286ff51b08c0314bdcf213707c0f/libmamba/tests/src/core/test_cpp.cpp#L149
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

    Upstream ``package_cache_folder_relative_path``:
    https://github.com/mamba-org/mamba/blob/e0172cadfb4c286ff51b08c0314bdcf213707c0f/libmamba/src/core/package_cache.cpp#L26

    >>> _normalize_url_for_cache_path("https://conda.anaconda.org/conda-forge/linux-64")
    'https/conda.anaconda.org/conda-forge/linux-64'
    >>> _normalize_url_for_cache_path("https://user:secret@example.com:8443/ch/noarch")
    'https/example.com_8443/ch/noarch'
    """
    cleaned = libmamba_strip_url_secrets(url)
    return cleaned.replace("://", "/").replace(":", "_").replace("\\", "_")


def normalize_url_for_compare(url: str) -> str:
    """Compare credential-free artifact URLs without conflating file and HTTP.

    Decode and re-encode each path segment so escaped epoch markers compare
    equal, while an encoded slash remains distinct from a path separator.
    """
    parsed = urlsplit(libmamba_strip_url_secrets(url))
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme
    path = "/".join(
        quote(unquote(part), safe="!$&'()*+,;=:@-._~")
        for part in parsed.path.split("/")
    )
    return urlunsplit(parsed._replace(scheme=scheme, path=path)).rstrip("/")


def hierarchical_cache_subpath(link_action: Mapping[str, Any]) -> pathlib.Path | None:
    """Return ``<normalized base url>/<subdir>`` for the mamba 2.6.0 layout.

    Prefers the LINK action's ``url`` (stripping the filename), falling back
    to ``base_url`` + ``platform``. Returns ``None`` when neither is usable.
    See ``package_cache_folder_relative_path`` linked above for the layout.

    >>> hierarchical_cache_subpath(
    ...     {"url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda"}
    ... ).as_posix()
    'https/conda.anaconda.org/conda-forge/linux-64'
    >>> hierarchical_cache_subpath(
    ...     {"base_url": "https://conda.anaconda.org/conda-forge", "platform": "linux-64"}
    ... ).as_posix()
    'https/conda.anaconda.org/conda-forge/linux-64'
    >>> hierarchical_cache_subpath({"name": "libzlib"}) is None
    True
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


def _link_action_explicit_or_derived_url(link_action: Mapping[str, Any]) -> str | None:
    """Best-effort URL for the linked package, with explicit-vs-derived
    intent baked into the name.

    Returns the LINK's explicit ``url`` (from any solver that emits
    the full repodata in LINK, i.e. mamba 2.x / micromamba) if
    present. Otherwise derives
    one from ``base_url``/``platform``/``fn`` -- this is the
    conda / Python-mamba-1.x
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
    record: Mapping[str, Any], link_action: Mapping[str, Any]
) -> tuple[bool, str | None]:
    """Require positive artifact identity and reject every known contradiction.

    A filename alone cannot distinguish two channels publishing the same build.
    Sparse conda LINKs instead provide a base URL and distribution name, which
    together identify the artifact location (the record supplies the extension).
    """
    for field in ("name", "version"):
        if not record.get(field) or record.get(field) != link_action.get(field):
            return False, f"{field} mismatch"
    link_subdir = link_action.get("subdir") or link_action.get("platform")
    if link_subdir and record.get("subdir") != link_subdir:
        return False, "subdir mismatch"
    for field in ("fn", "md5", "sha256", "build", "build_string"):
        expected, actual = link_action.get(field), record.get(field)
        if expected and actual and expected != actual:
            return False, f"{field} mismatch"
    filename = record.get("fn") or ""
    stem = filename.removesuffix(".tar.bz2").removesuffix(".conda")
    if link_action.get("dist_name") and stem != link_action["dist_name"]:
        return False, "dist_name mismatch"
    link_url = _link_action_explicit_or_derived_url(link_action)
    if not link_url and link_action.get("base_url") and filename:
        link_url = _link_action_explicit_or_derived_url({**link_action, "fn": filename})
    record_url = record.get("url")
    if link_url and record_url:
        if normalize_url_for_compare(link_url) != normalize_url_for_compare(record_url):
            return False, "url mismatch"
        return True, None
    # A matching digest is positive evidence even if one side has no URL.
    for field in ("sha256", "md5"):
        if record.get(field) and record.get(field) == link_action.get(field):
            return True, None
    return False, "no positive artifact identity (URL or digest)"


def record_validation_error(record: Any) -> str | None:
    """Validate untyped JSON before treating it as a FETCH record.

    Missing/null dependencies are unknown, whereas an explicit empty list can
    be legitimate. No fingerprint can establish arbitrary metadata correctness.
    """
    if not isinstance(record, Mapping):
        return "record must be a JSON object"
    for key in ("name", "version", "url", "fn", "subdir"):
        if not isinstance(record.get(key), str) or not record[key]:
            return f"missing or invalid {key}"
    depends = record.get("depends")
    if not isinstance(depends, list) or any(
        not isinstance(dep, str) or not dep.strip() for dep in depends
    ):
        return "depends must be an explicit list of dependency strings"
    constrains = record.get("constrains")
    if constrains is not None and (
        not isinstance(constrains, list)
        or any(not isinstance(spec, str) or not spec.strip() for spec in constrains)
    ):
        return "constrains must be a list of dependency strings"
    if not any(
        isinstance(record.get(key), str) and record[key] for key in ("md5", "sha256")
    ):
        return "missing artifact digest"
    for key in ("md5", "sha256"):
        if record.get(key) is not None and not isinstance(record[key], str):
            return f"invalid {key}"
    if (
        unquote(pathlib.PurePosixPath(urlsplit(record["url"]).path).name)
        != record["fn"]
    ):
        return "url/filename mismatch"
    filename = record["fn"]
    if not filename.endswith((".conda", ".tar.bz2")):
        return "unsupported artifact filename"
    stem = filename.removesuffix(".conda").removesuffix(".tar.bz2")
    parts = stem.rsplit("-", 2)
    if len(parts) != 3 or parts[:2] != [record["name"], record["version"]]:
        return "name/version disagree with artifact filename"
    if any(
        record.get(key) and record[key] != parts[2] for key in ("build", "build_string")
    ):
        return "build disagrees with artifact filename"
    if (
        pathlib.PurePosixPath(urlsplit(record["url"]).path).parent.name
        != record["subdir"]
    ):
        return "subdir disagrees with artifact URL"
    return None


def candidate_record_paths(
    pkgs_dir: pathlib.Path,
    dist_name: str,
    link_action: Mapping[str, Any],
) -> list[pathlib.Path]:
    """Candidate ``repodata_record.json`` locations, in priority order.

    Conda and pre-2.6 mamba use a flat layout (``<pkgs_dir>/<dist_name>/...``).
    Mamba/micromamba 2.6.0 nests packages under the channel and subdir
    derived from the package URL (see mamba-org/mamba#4163). We compute the
    expected hierarchical path from the LINK metadata rather than walking the
    cache, then fall back to the legacy flat path.

    >>> import pathlib
    >>> [
    ...     p.as_posix()
    ...     for p in candidate_record_paths(
    ...         pathlib.Path("/pkgs"),
    ...         "libzlib-1.3.2-h25fd6f3_2",
    ...         {"url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda"},
    ...     )
    ... ]  # doctest: +NORMALIZE_WHITESPACE
    ['/pkgs/https/conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2/info/repodata_record.json',
     '/pkgs/libzlib-1.3.2-h25fd6f3_2/info/repodata_record.json']
    >>> [
    ...     p.as_posix()
    ...     for p in candidate_record_paths(
    ...         pathlib.Path("/pkgs"), "libzlib-1.3.2-h25fd6f3_2", {"name": "libzlib"}
    ...     )
    ... ]
    ['/pkgs/libzlib-1.3.2-h25fd6f3_2/info/repodata_record.json']
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
    link_action: Mapping[str, Any],
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

    A candidate that exists but fails to parse as JSON is skipped
    (logged at DEBUG) rather than treated as a hard error, for the
    same reason the retry loop exists: the solver may still be
    writing the record (the write is not atomic), and another
    candidate path or ``pkgs_dir`` -- or the same path 0.1s later --
    may hold a good copy. Aborting the whole lookup on the first
    unparseable file would turn a transient partial write into a
    hard failure even when a valid record is available. If every
    candidate fails, the lookup still fails loudly via the
    giving-up WARNING below.
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
                invalid = record_validation_error(record)
                if invalid:
                    last_rejected = f"invalid metadata at {candidate}: {invalid}"
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
