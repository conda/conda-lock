"""Local package-cache I/O for the conda-lock dryrun pipeline.

This module owns the question "what does the on-disk cache say about a
given LINK action?" -- nothing else. Path derivation across the
legacy flat layout and the mamba 2.6.0 hierarchical layout
(see https://github.com/mamba-org/mamba/pull/4163) and
``repodata_record.json`` lookup live here.

Strict layering: this module imports only from ``models`` and
``invoke_conda``. Subprocess probing of the conda CLI
(``get_pkgs_dirs``) and JSON-stdout parsing (``extract_json_object``)
live in ``conda_lock.invoke_conda`` since they are CLI concerns
rather than cache-record concerns.
"""

import json
import logging
import pathlib
import time

from conda_lock.models.dry_run_install import FetchAction, LinkAction


logger = logging.getLogger(__name__)


def _normalize_url_for_cache_path(url: str) -> str:
    """Apply mamba 2.6.0's URL normalization for package cache paths.

    Mirrors libmamba's ``package_cache_folder_relative_path``: scheme
    separators ``://`` become ``/`` and remaining ``:`` / ``\\`` are
    replaced with ``_``. Path separators are preserved.
    """
    return url.replace("://", "/").replace(":", "_").replace("\\", "_")


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

    On rare occasion during CI tests, conda fails to find a package
    in the package cache (perhaps because the package is still being
    processed); waiting 0.1 seconds resolves it. Allow up to a full
    second to elapse before giving up.
    """
    NUM_RETRIES = 10
    for retry in range(1, NUM_RETRIES + 1):
        for pkgs_dir in pkgs_dirs:
            for candidate in candidate_record_paths(pkgs_dir, dist_name, link_action):
                if not candidate.is_file():
                    continue
                try:
                    with open(candidate) as f:
                        record: FetchAction = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                return record
        logger.debug(
            f"Failed to find repodata_record.json for {dist_name}. "
            f"Retrying in 0.1 seconds ({retry}/{NUM_RETRIES})"
        )
        time.sleep(0.1)
    logger.warning(
        f"Failed to find repodata_record.json for {dist_name}. Giving up."
    )
    return None
