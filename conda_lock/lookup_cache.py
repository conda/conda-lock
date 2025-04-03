import hashlib
import logging
import platform
import re

from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from filelock import FileLock, Timeout
from platformdirs import user_cache_path


logger = logging.getLogger(__name__)


CLEAR_CACHE_AFTER_SECONDS = 60 * 60 * 24 * 2  # 2 days
"""Cached files older than this will be deleted."""

DONT_CHECK_IF_NEWER_THAN_SECONDS = 60 * 5  # 5 minutes
"""If the cached file is newer than this, just use it without checking for updates."""

WINDOWS_TIME_EPSILON = 0.005
"""Windows has issues with file timestamps, so we add this small offset
to ensure that newly created files have a positive age.
"""


def uncached_download_file(url: str) -> bytes:
    """The simple equivalent to cached_download_file."""
    res = requests.get(url, headers={"User-Agent": "conda-lock"})
    res.raise_for_status()
    return res.content


def cached_download_file(
    url: str,
    *,
    cache_subdir_name: str,
    cache_root: Optional[Path] = None,
    max_age_seconds: float = CLEAR_CACHE_AFTER_SECONDS,
    dont_check_if_newer_than_seconds: float = DONT_CHECK_IF_NEWER_THAN_SECONDS,
) -> bytes:
    """Download a file and cache it in the user cache directory.

    If the file is already cached, return the cached contents.
    If the file is not cached, download it and cache the contents
    and the ETag.

    Protect against multiple processes downloading the same file.
    """
    if cache_root is None:
        cache_root = user_cache_path("conda-lock", appauthor=False)
    cache = cache_root / "cache" / cache_subdir_name
    cache.mkdir(parents=True, exist_ok=True)
    clear_old_files_from_cache(cache, max_age_seconds=max_age_seconds)

    destination_lock = (cache / cached_filename_for_url(url)).with_suffix(".lock")

    # Wait for any other process to finish downloading the file.
    # This way we can use the result from the current download without
    # spawning multiple concurrent downloads.
    while True:
        try:
            with FileLock(str(destination_lock), timeout=5):
                return _download_to_or_read_from_cache(
                    url,
                    cache=cache,
                    dont_check_if_newer_than_seconds=dont_check_if_newer_than_seconds,
                )
        except Timeout:
            logger.warning(
                f"Failed to acquire lock on {destination_lock}, it is likely "
                f"being downloaded by another process. Retrying..."
            )


def _download_to_or_read_from_cache(
    url: str, *, cache: Path, dont_check_if_newer_than_seconds: float
) -> bytes:
    """Download a file to the cache directory and return the contents.

    This function is designed to be called from within a FileLock context to avoid
    multiple processes downloading the same file.

    If the file is newer than `dont_check_if_newer_than_seconds`, then immediately
    return the cached contents. Otherwise we pass the ETag from the last download
    in the headers to avoid downloading the file if it hasn't changed remotely.
    """
    destination = cache / cached_filename_for_url(url)
    destination_etag = destination.with_suffix(".etag")
    request_headers = {"User-Agent": "conda-lock"}
    # Return the contents immediately if the file is fresh
    if destination.is_file():
        age_seconds = get_age_seconds(destination)
        if age_seconds is None:
            raise RuntimeError(f"Error checking age of {destination}")
        if 0 <= age_seconds < dont_check_if_newer_than_seconds:
            logger.debug(
                f"Using cached mapping {destination} of age {age_seconds}s "
                f"without checking for updates"
            )
            return destination.read_bytes()
        # Add the ETag from the last download, if it exists, to the headers.
        # The ETag is used to avoid downloading the file if it hasn't changed remotely.
        # Otherwise, download the file and cache the contents and ETag.
        if destination_etag.is_file():
            old_etag = destination_etag.read_text().strip()
            request_headers["If-None-Match"] = old_etag
    # Download the file and cache the result.
    logger.debug(f"Requesting {url}")
    res = requests.get(url, headers=request_headers)
    if res.status_code == 304:
        logger.debug(f"{url} has not changed since last download, using {destination}")
    else:
        res.raise_for_status()
        destination.write_bytes(res.content)
        if "ETag" in res.headers:
            destination_etag.write_text(res.headers["ETag"])
        else:
            logger.warning("No ETag in response headers")
    logger.debug(f"Downloaded {url} to {destination}")
    return destination.read_bytes()


def cached_filename_for_url(url: str) -> str:
    """Return a filename for a URL that is probably unique to the URL.

    The filename is a 4-character hash of the URL, followed by the extension.
    If the extension is not alphanumeric or too long, it is omitted.

    >>> cached_filename_for_url("https://example.com/foo.json")
    'a5d7.json'
    >>> cached_filename_for_url("https://example.com/foo")
    '5ea6'
    >>> cached_filename_for_url("https://example.com/foo.bär")
    '2191'
    >>> cached_filename_for_url("https://example.com/foo.baaaaaar")
    '1861'
    """
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:4]
    extension = url.split(".")[-1]
    if len(extension) <= 6 and re.match("^[a-zA-Z0-9]+$", extension):
        return f"{url_hash}.{extension}"
    else:
        return f"{url_hash}"


def clear_old_files_from_cache(cache: Path, *, max_age_seconds: float) -> None:
    """Remove files in the cache directory older than `max_age_seconds`.

    Also removes any files that somehow have a modification time in the future.

    For safety, this raises an error if `cache` is not a subdirectory of
    a directory named `"cache"`.
    """
    if not cache.parent.name == "cache":
        raise ValueError(
            f"Expected cache directory, got {cache}. Parent should be 'cache' ",
            f"not '{cache.parent.name}'",
        )
    for file in cache.iterdir():
        age_seconds = get_age_seconds(file)
        if age_seconds is None:
            # The file was probably already deleted.
            pass
        elif age_seconds < 0 or age_seconds >= max_age_seconds:
            try:
                file.unlink()
                logger.debug(f"Removed old cache file {file} of age {age_seconds}s")
            except FileNotFoundError:
                pass


def get_age_seconds(path: Path) -> Optional[float]:
    """Return the age of a file in seconds.

    On Windows, the age of a new file is sometimes slightly negative, so we add a small
    offset to ensure that the age is positive.
    """
    try:
        raw_age = datetime.now().timestamp() - path.stat().st_mtime
    except FileNotFoundError:
        return None
    if platform.system() == "Windows":
        return raw_age + WINDOWS_TIME_EPSILON
    else:
        return raw_age
