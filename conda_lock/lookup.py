import hashlib
import json
import logging
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
    current_time = time.time()
    cache = user_cache_path("conda-lock", appauthor=False)
    cache.mkdir(parents=True, exist_ok=True)

    # clear out old cache files
    for file in cache.iterdir():
        if file.name.startswith("pypi-mapping-"):
            mtime = file.stat().st_mtime
            age = current_time - mtime
            if age < 0 or age > CLEAR_CACHE_AFTER_SECONDS:
                logger.debug("Removing old cache file %s", file)
                file.unlink()

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:4]
    destination_mapping = cache / f"pypi-mapping-{url_hash}.yaml"
    destination_etag = destination_mapping.with_suffix(".etag")
    destination_lock = destination_mapping.with_suffix(".lock")

    # Wait for any other process to finish downloading the file.
    # Use the ETag to avoid downloading the file if it hasn't changed.
    # Otherwise, download the file and cache the contents and ETag.
    while True:
        try:
            with FileLock(destination_lock, timeout=5):
                # Return the contents immediately if the file is fresh
                try:
                    mtime = destination_mapping.stat().st_mtime
                    age = current_time - mtime
                    if age < DONT_CHECK_IF_NEWER_THAN_SECONDS:
                        contents = destination_mapping.read_bytes()
                        logger.debug(
                            f"Using cached mapping {destination_mapping} without "
                            f"checking for updates"
                        )
                        return contents
                except FileNotFoundError:
                    pass
                # Get the ETag from the last download, if it exists
                if destination_mapping.exists() and destination_etag.exists():
                    logger.debug(f"Old ETag found at {destination_etag}")
                    try:
                        old_etag = destination_etag.read_text().strip()
                        headers = {"If-None-Match": old_etag}
                    except FileNotFoundError:
                        logger.warning("Failed to read ETag")
                        headers = {}
                else:
                    headers = {}
                # Download the file and cache the result.
                logger.debug(f"Requesting {url}")
                res = requests.get(url, headers=headers)
                if res.status_code == 304:
                    logger.debug(
                        f"{url} has not changed since last download, "
                        f"using {destination_mapping}"
                    )
                else:
                    res.raise_for_status()
                    time.sleep(10)
                    destination_mapping.write_bytes(res.content)
                    if "ETag" in res.headers:
                        destination_etag.write_text(res.headers["ETag"])
                    else:
                        logger.warning("No ETag in response headers")
                logger.debug(f"Downloaded {url} to {destination_mapping}")
                return destination_mapping.read_bytes()

        except Timeout:
            logger.warning(
                f"Failed to acquire lock on {destination_lock}, it is likely "
                f"being downloaded by another process. Retrying..."
            )
