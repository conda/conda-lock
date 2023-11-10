from collections import ChainMap
from contextlib import suppress
from functools import cached_property
from typing import Dict, Mapping, Optional, Union, cast

import requests
import yaml

from packaging.utils import NormalizedName, canonicalize_name
from typing_extensions import NotRequired, TypedDict


class MappingEntry(TypedDict):
    conda_name: str
    # legacy field, generally not used by anything anymore
    conda_forge: NotRequired[str]
    pypi_name: NormalizedName


class _LookupLoader:
    _mapping_url: str = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"
    _local_mappings: Optional[Dict[NormalizedName, MappingEntry]] = None

    @property
    def mapping_url(self) -> str:
        return self._mapping_url

    @mapping_url.setter
    def mapping_url(self, value: str) -> None:
        # these will raise AttributeError if they haven't been cached yet.
        with suppress(AttributeError):
            del self.remote_mappings
        with suppress(AttributeError):
            del self.conda_lookup
        self._mapping_url = value

    @cached_property
    def remote_mappings(self) -> Dict[NormalizedName, MappingEntry]:
        """PyPI to conda name mapping fetched from `_mapping_url`"""
        res = requests.get(self._mapping_url)
        res.raise_for_status()
        lookup = yaml.safe_load(res.content)
        # lowercase and kebabcase the pypi names
        assert lookup is not None
        lookup = {canonicalize_name(k): v for k, v in lookup.items()}
        for v in lookup.values():
            v["pypi_name"] = canonicalize_name(v["pypi_name"])
        return lookup

    @property
    def local_mappings(self) -> Dict[NormalizedName, MappingEntry]:
        """PyPI to conda name mappings set by the user."""
        return self._local_mappings or {}

    @local_mappings.setter
    def local_mappings(self, mappings: Mapping[str, Union[str, MappingEntry]]) -> None:
        """Value should be a mapping from pypi name to conda name or a mapping entry."""
        lookup: Dict[NormalizedName, MappingEntry] = {}
        # normalize to Dict[NormalizedName, MappingEntry]
        for k, v in mappings.items():
            key = canonicalize_name(k)
            if isinstance(v, Mapping):
                if "conda_name" not in v or "pypi_name" not in v:
                    raise ValueError(
                        "MappingEntries must have both a 'conda_name' and 'pypi_name'"
                    )
                entry = cast("MappingEntry", dict(v))
                entry["pypi_name"] = canonicalize_name(str(entry["pypi_name"]))
            elif isinstance(v, str):
                entry = {"conda_name": v, "pypi_name": key}
            else:
                raise TypeError(
                    "Each entry in the mapping must be a string or a mapping"
                )
            lookup[key] = entry
        self._local_mappings = lookup

    @property
    def pypi_lookup(self) -> Mapping[NormalizedName, MappingEntry]:
        """ChainMap of PyPI to conda name mappings.

        Local mappings take precedence over remote mappings fetched from `_mapping_url`.
        """
        return ChainMap(self.local_mappings, self.remote_mappings)

    @cached_property
    def conda_lookup(self) -> Dict[str, MappingEntry]:
        return {record["conda_name"]: record for record in self.pypi_lookup.values()}


LOOKUP_OBJECT = _LookupLoader()


def get_forward_lookup() -> Mapping[NormalizedName, MappingEntry]:
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


def set_pypi_lookup_overrides(mappings: Mapping[str, Union[str, MappingEntry]]) -> None:
    """Set overrides to the pypi lookup"""
    global LOOKUP_OBJECT
    # type ignore because the setter will normalize the types
    LOOKUP_OBJECT.local_mappings = mappings  # type: ignore [assignment]


def conda_name_to_pypi_name(name: str) -> NormalizedName:
    """return the pypi name for a conda package"""
    lookup = get_lookup()
    cname = canonicalize_name(name)
    return lookup.get(cname, {"pypi_name": cname})["pypi_name"]


def pypi_name_to_conda_name(name: str) -> str:
    """return the conda name for a pypi package"""
    cname = canonicalize_name(name)
    return get_forward_lookup().get(cname, {"conda_name": cname})["conda_name"]
