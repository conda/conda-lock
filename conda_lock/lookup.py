import hashlib
import json
import logging
import re
import time

from functools import lru_cache
from pathlib import Path
from typing import Dict, TypedDict

import requests

from filelock import FileLock, Timeout
from packaging.utils import NormalizedName
from packaging.utils import canonicalize_name as canonicalize_pypi_name
from platformdirs import user_cache_path


logger = logging.getLogger(__name__)

DEFAULT_MAPPING_URL = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.json"

CLEAR_CACHE_AFTER_SECONDS = 60 * 60 * 24 * 2  # 2 days
"""Cached files older than this will be deleted."""

DONT_CHECK_IF_NEWER_THAN_SECONDS = 60 * 5  # 5 minutes
"""If the cached file is newer than this, just use it without checking for updates."""


class MappingEntry(TypedDict):
    conda_name: str
    # legacy field, generally not used by anything anymore
    conda_forge: str
    pypi_name: NormalizedName


@lru_cache(maxsize=None)
def _get_pypi_lookup(mapping_url: str) -> Dict[NormalizedName, MappingEntry]:
    url = mapping_url
    if url.startswith("http://") or url.startswith("https://"):
        content = cached_download_file(url)
    else:
        if url.startswith("file://"):
            path = url[len("file://") :]
        else:
            path = url
        content = Path(path).read_bytes()
    logger.debug("Parsing PyPI mapping")
    load_start = time.monotonic()
    if url.endswith(".json"):
        lookup = json.loads(content)
    else:
        import ruamel.yaml

        yaml = ruamel.yaml.YAML(typ="safe")
        lookup = yaml.load(content)
    load_duration = time.monotonic() - load_start
    logger.debug(f"Loaded {len(lookup)} entries in {load_duration:.2f}s")
    # lowercase and kebabcase the pypi names
    assert lookup is not None
    lookup = {canonicalize_pypi_name(k): v for k, v in lookup.items()}
    for v in lookup.values():
        v["pypi_name"] = canonicalize_pypi_name(v["pypi_name"])
    return lookup


def pypi_name_to_conda_name(name: str, mapping_url: str) -> str:
    """Convert a PyPI package name to a conda package name.

    >>> from conda_lock.lookup import DEFAULT_MAPPING_URL
    >>> pypi_name_to_conda_name("build", mapping_url=DEFAULT_MAPPING_URL)
    'python-build'

    >>> pypi_name_to_conda_name("zpfqzvrj", mapping_url=DEFAULT_MAPPING_URL)
    'zpfqzvrj'
    """
    cname = canonicalize_pypi_name(name)
    lookup = _get_pypi_lookup(mapping_url)
    if cname in lookup:
        entry = lookup[cname]
        res = entry.get("conda_name") or entry.get("conda_forge")
        if res is not None:
            return res

    logger.warning(f"Could not find conda name for {cname}. Assuming identity.")
    return cname


@lru_cache(maxsize=None)
def _get_conda_lookup(mapping_url: str) -> Dict[str, MappingEntry]:
    """
    Reverse grayskull name mapping to map conda names onto PyPI
    """
    return {
        record["conda_name"]: record
        for record in _get_pypi_lookup(mapping_url).values()
    }


def conda_name_to_pypi_name(name: str, mapping_url: str) -> NormalizedName:
    """return the pypi name for a conda package"""
    lookup = _get_conda_lookup(mapping_url=mapping_url)
    cname = canonicalize_pypi_name(name)
    return lookup.get(cname, {"pypi_name": cname})["pypi_name"]


def cached_download_file(url: str) -> bytes:
    """Download a file and cache it in the user cache directory.

    If the file is already cached, return the cached contents.
    If the file is not cached, download it and cache the contents
    and the ETag.

    Protect against multiple processes downloading the same file.
    """
    cache = user_cache_path("conda-lock", appauthor=False) / "cache" / "pypi-mapping"
    cache.mkdir(parents=True, exist_ok=True)
    clear_old_files_from_cache(cache, max_age=CLEAR_CACHE_AFTER_SECONDS)

    destination_lock = (cache / cached_filename_for_url(url)).with_suffix(".lock")

    # Wait for any other process to finish downloading the file.
    # This way we can use the result from the current download without
    # spawning multiple concurrent downloads.
    while True:
        try:
            with FileLock(destination_lock, timeout=5):
                return download_to_or_read_from_cache(url, cache)
        except Timeout:
            logger.warning(
                f"Failed to acquire lock on {destination_lock}, it is likely "
                f"being downloaded by another process. Retrying..."
            )


def download_to_or_read_from_cache(url: str, cache: Path) -> bytes:
    """Download a file to the cache directory and return the contents.

    If the file is newer than DONT_CHECK_IF_NEWER_THAN_SECONDS, then immediately
    return the cached contents. Otherwise we pass the ETag from the last download
    in the headers to avoid downloading the file if it hasn't changed remotely.
    """
    destination = cache / cached_filename_for_url(url)
    destination_etag = destination.with_suffix(".etag")
    request_headers = {}
    # Return the contents immediately if the file is fresh
    if destination.is_file():
        mtime = destination.stat().st_mtime
        age = time.time() - mtime
        if age < DONT_CHECK_IF_NEWER_THAN_SECONDS:
            logger.debug(
                f"Using cached mapping {destination} without checking for updates"
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


def clear_old_files_from_cache(cache: Path, *, max_age: float) -> None:
    """Remove files in the cache directory older than max_age seconds.

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
        mtime = file.stat().st_mtime
        age = time.time() - mtime
        if age < 0 or age > max_age:
            logger.debug("Removing old cache file %s", file)
            file.unlink()
