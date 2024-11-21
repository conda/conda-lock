import json
import logging
import time

from functools import lru_cache
from pathlib import Path
from typing import Dict, TypedDict

from packaging.utils import NormalizedName
from packaging.utils import canonicalize_name as canonicalize_pypi_name

from conda_lock.lookup_cache import cached_download_file


logger = logging.getLogger(__name__)

DEFAULT_MAPPING_URL = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.json"


class MappingEntry(TypedDict):
    conda_name: str
    # legacy field, generally not used by anything anymore
    conda_forge: str
    pypi_name: NormalizedName


@lru_cache(maxsize=None)
def _get_pypi_lookup(mapping_url: str) -> Dict[NormalizedName, MappingEntry]:
    url = mapping_url
    if url.startswith("http://") or url.startswith("https://"):
        content = cached_download_file(url, cache_subdir_name="pypi-mapping")
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
