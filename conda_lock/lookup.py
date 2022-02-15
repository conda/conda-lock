from typing import Dict, Optional

import requests
import yaml


PYPI_LOOKUP: Optional[Dict] = None

# TODO: make this configurable
PYPI_TO_CONDA_NAME_LOOKUP = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"


def get_forward_lookup():
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
    global PYPI_LOOKUP
    if PYPI_LOOKUP is None:
        PYPI_LOOKUP = {
            record["conda_name"]: record for record in get_forward_lookup().values()
        }
    return PYPI_LOOKUP


def normalize_conda_name(name: str):
    return get_lookup().get(name, {"pypi_name": name})["pypi_name"]
