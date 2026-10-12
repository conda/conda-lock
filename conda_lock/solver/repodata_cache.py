"""Locate and validate artifact records across flat and hierarchical caches.

Identity checks here establish which artifact a cached record describes.
Channel metadata verification is handled by channel_metadata.py.
"""

import logging
import pathlib
import re

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit


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
