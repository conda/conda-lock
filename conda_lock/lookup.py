from typing import Dict, Optional

import requests
import yaml


class _LookupLoader:
    def __init__(self) -> None:
        self._mapping_url = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"
        self._pypi_lookup: Optional[dict] = None
        self._conda_lookup: Optional[dict] = None

    def resolve(self) -> None:
        res = requests.get(self._mapping_url)
        res.raise_for_status()
        self._pypi_lookup = yaml.safe_load(res.content)

    @property
    def pypi_lookup(self) -> dict:
        if not self._pypi_lookup:
            self.resolve()
            assert isinstance(self._pypi_lookup, dict)
        return self._pypi_lookup

    @property
    def conda_lookup(self) -> dict:
        if not self._conda_lookup:
            self._conda_lookup = {
                record["conda_name"]: record for record in self.pypi_lookup.values()
            }
            assert isinstance(self._conda_lookup, dict)
        return self._conda_lookup

    def set_lookup(self, lookup_url: str) -> None:
        self._pypi_lookup = None
        self._conda_lookup = None
        self._mapping_url = lookup_url


LOOKUP_OBJECT = _LookupLoader()


def get_forward_lookup() -> Dict:
    global LOOKUP_OBJECT
    return LOOKUP_OBJECT.pypi_lookup


def get_lookup() -> Dict:
    """
    Reverse grayskull name mapping to map conda names onto PyPI
    """
    global LOOKUP_OBJECT
    return LOOKUP_OBJECT.conda_lookup


def set_lookup_location(lookup_url: str) -> None:
    global LOOKUP_OBJECT
    LOOKUP_OBJECT.set_lookup(lookup_url)


def conda_name_to_pypi_name(name: str) -> str:
    """return the pypi name for a conda package"""
    lookup = get_lookup()
    return lookup.get(name, {"pypi_name": name})["pypi_name"]


def pypi_name_to_conda_name(name: str) -> str:
    """return the conda name for a pypi package"""
    return get_forward_lookup().get(name, {"conda_name": name})["conda_name"]
