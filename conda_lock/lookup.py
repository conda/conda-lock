from functools import cached_property
from pathlib import Path
from typing import Dict

import requests
import yaml

from packaging.utils import NormalizedName, canonicalize_name
from typing_extensions import TypedDict


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
            res = requests.get(self._mapping_url)
            res.raise_for_status()
            content = res.content
        else:
            if url.startswith("file://"):
                path = url[len("file://") :]
            else:
                path = url
            content = Path(path).read_bytes()
        lookup = yaml.safe_load(content)
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
