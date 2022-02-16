from typing import Dict, Optional

import requests
import yaml


PYPI_LOOKUP: Optional[Dict] = None
CONDA_LOOKUP: Optional[Dict] = None

# TODO: make this configurable
PYPI_TO_CONDA_NAME_LOOKUP = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"


def get_forward_lookup() -> Dict:
    global PYPI_LOOKUP
    if PYPI_LOOKUP is None:
        res = requests.get(PYPI_TO_CONDA_NAME_LOOKUP)
        res.raise_for_status()
        PYPI_LOOKUP = yaml.safe_load(res.content)
    return PYPI_LOOKUP


def get_lookup() -> Dict:
    """
    Reverse grayskull name mapping to map conda names onto PyPI
    """
    global CONDA_LOOKUP
    if CONDA_LOOKUP is None:
        CONDA_LOOKUP = {
            record["conda_name"]: record for record in get_forward_lookup().values()
        }
    return CONDA_LOOKUP


def conda_name_to_pypi_name(name: str) -> str:
    """return the pypi name for a conda package"""
    lookup = get_lookup()
    return lookup.get(name, {"pypi_name": name})["pypi_name"]


def pypi_name_to_conda_name(name: str) -> str:
    """return the conda name for a pypi package"""
    return get_forward_lookup().get(name, {"conda_name": name})["conda_name"]
