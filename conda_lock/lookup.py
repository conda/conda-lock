import hashlib
import logging
import time

from functools import cached_property
from pathlib import Path
from typing import Dict

import requests
import ruamel.yaml

from filelock import FileLock, Timeout
from packaging.utils import NormalizedName, canonicalize_name
from platformdirs import user_cache_path
from typing_extensions import TypedDict


logger = logging.getLogger(__name__)


class MappingEntry(TypedDict):
    conda_name: str
    # legacy field, generally not used by anything anymore
    conda_forge: str
    pypi_name: NormalizedName


class _LookupLoader:
    _mapping_url: str = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"

    @property
    def mapping_url(self) -> str:
        return self._mapping_url

    @mapping_url.setter
    def mapping_url(self, value: str) -> None:
        if self._mapping_url != value:
            self._mapping_url = value
            # Invalidate cache
            try:
                del self.pypi_lookup
            except AttributeError:
                pass
            try:
                del self.conda_lookup
            except AttributeError:
                pass

    @cached_property
    def pypi_lookup(self) -> Dict[NormalizedName, MappingEntry]:
        url = self.mapping_url
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
        yaml = ruamel.yaml.YAML(typ="safe")
        lookup = yaml.load(content)
        load_duration = time.monotonic() - load_start
        logger.debug(f"Loaded {len(lookup)} entries in {load_duration:.2f}s")
        # lowercase and kebabcase the pypi names
        assert lookup is not None
        lookup = {canonicalize_name(k): v for k, v in lookup.items()}
        for v in lookup.values():
            v["pypi_name"] = canonicalize_name(v["pypi_name"])
        return lookup

    @cached_property
    def conda_lookup(self) -> Dict[str, MappingEntry]:
        return {record["conda_name"]: record for record in self.pypi_lookup.values()}


LOOKUP_OBJECT = _LookupLoader()


def get_forward_lookup() -> Dict[NormalizedName, MappingEntry]:
    global LOOKUP_OBJECT
    return LOOKUP_OBJECT.pypi_lookup


def get_lookup() -> Dict[str, MappingEntry]:
    """
    Reverse grayskull name mapping to map conda names onto PyPI
    """
    global LOOKUP_OBJECT
    return LOOKUP_OBJECT.conda_lookup


def set_lookup_location(lookup_url: str) -> None:
    global LOOKUP_OBJECT
    LOOKUP_OBJECT.mapping_url = lookup_url


def conda_name_to_pypi_name(name: str) -> NormalizedName:
    """return the pypi name for a conda package"""
    lookup = get_lookup()
    cname = canonicalize_name(name)
    return lookup.get(cname, {"pypi_name": cname})["pypi_name"]


def pypi_name_to_conda_name(name: str) -> str:
    """return the conda name for a pypi package"""
    cname = canonicalize_name(name)
    return get_forward_lookup().get(cname, {"conda_name": cname})["conda_name"]


def cached_download_file(url: str) -> bytes:
    """Download a file and cache it in the user cache directory.

    If the file is already cached, return the cached contents.
    If the file is not cached, download it and cache the contents
    and the ETag.

    Protect against multiple processes downloading the same file.
    """
    CLEAR_CACHE_AFTER_SECONDS = 60 * 60 * 24 * 2  # 2 days
    DONT_CHECK_IF_NEWER_THAN_SECONDS = 60 * 5  # 5 minutes
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

    # Wait for any other process to finish downloading the file.
    # Use the ETag to avoid downloading the file if it hasn't changed.
    # Otherwise, download the file and cache the contents and ETag.
    while True:
        try:
            with FileLock(destination_lock, timeout=5):
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
                f"being downloaded by another process. Retrying...",
                destination_lock,
            )
