import logging

from contextlib import suppress
from functools import cached_property
from typing import Dict, Union, cast
from pathlib import Path

import requests
import yaml

from packaging.utils import NormalizedName, canonicalize_name
from typing_extensions import TypedDict


DEFAULT_MAPPING_URL = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"


class MappingEntry(TypedDict):
    conda_name: str
    pypi_name: NormalizedName


class _LookupLoader:
    """Object used to map PyPI package names to conda names."""

    mapping_url: str
    local_mappings: Dict[NormalizedName, MappingEntry]

    def __init__(self) -> None:
        self.mapping_url = DEFAULT_MAPPING_URL
        self.local_mappings = {}

    @cached_property
    def remote_mappings(self) -> Dict[NormalizedName, MappingEntry]:
        """PyPI to conda name mapping fetched from `mapping_url`"""
        res = requests.get(self.mapping_url)
        res.raise_for_status()
        lookup = yaml.safe_load(res.content)
        # lowercase and kebabcase the pypi names
        assert lookup is not None
        lookup = {canonicalize_name(k): v for k, v in lookup.items()}
        for v in lookup.values():
            v["pypi_name"] = canonicalize_name(v["pypi_name"])
        return lookup

    @property
    def pypi_lookup(self) -> Dict[NormalizedName, MappingEntry]:
        """Dict of PyPI to conda name mappings.

        Local mappings take precedence over remote mappings fetched from `mapping_url`.
        """
        return {**self.remote_mappings, **self.local_mappings}

    @cached_property
    def conda_lookup(self) -> Dict[str, MappingEntry]:
        return {record["conda_name"]: record for record in self.pypi_lookup.values()}


_lookup_loader = _LookupLoader()


def set_lookup_location(lookup_url: str) -> None:
    """Set the location of the pypi lookup

    Used by the `lock` cli command to override the DEFAULT_MAPPING_URL for the lookup.
    """
    # these will raise AttributeError if they haven't been cached yet.
    with suppress(AttributeError):
        del _lookup_loader.remote_mappings
    with suppress(AttributeError):
        del _lookup_loader.conda_lookup
    _lookup_loader.mapping_url = lookup_url


def set_pypi_lookup_overrides(mappings: Dict[str, Union[str, MappingEntry]]) -> None:
    """Set overrides to the pypi lookup"""
    lookup: Dict[NormalizedName, MappingEntry] = {}
    # normalize to Dict[NormalizedName, MappingEntry]
    for k, v in mappings.items():
        key = canonicalize_name(k)
        if isinstance(v, dict):
            if "conda_name" not in v or "pypi_name" not in v:
                raise ValueError(
                    "MappingEntries must have both a 'conda_name' and 'pypi_name'"
                )
            entry = cast("MappingEntry", dict(v))
            entry["pypi_name"] = canonicalize_name(str(entry["pypi_name"]))
        elif isinstance(v, str):
            entry = {"conda_name": v, "pypi_name": key}
        else:
            raise TypeError("Each entry in the mapping must be a string or a dict")
        lookup[key] = entry
    _lookup_loader.local_mappings = lookup


def conda_name_to_pypi_name(name: str) -> NormalizedName:
    """return the pypi name for a conda package"""
    lookup = _lookup_loader.conda_lookup
    cname = canonicalize_name(name)
    return lookup.get(cname, {"pypi_name": cname})["pypi_name"]


def pypi_name_to_conda_name(name: str) -> str:
    """return the conda name for a pypi package"""
    cname = canonicalize_name(name)
    forward_lookup = _lookup_loader.pypi_lookup
    if cname not in forward_lookup:
        logging.warning(f"Could not find conda name for {cname!r}. Assuming identity.")
        return cname
    return forward_lookup[cname]["conda_name"]
