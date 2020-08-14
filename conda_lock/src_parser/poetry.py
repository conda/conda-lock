import collections
import collections.abc
import copy
import pathlib

from typing import List, Optional

import requests
import toml
import yaml

from conda_lock.src_parser import LockSpecification


# TODO: make this configurable
PYPI_TO_CONDA_NAME_LOOKUP = "https://raw.githubusercontent.com/marcelotrevisani/grayskull/master/grayskull/pypi/config.yaml"
PYPI_LOOKUP: Optional[dict] = None


def get_lookup():
    global PYPI_LOOKUP
    if PYPI_LOOKUP is None:
        res = requests.get(PYPI_TO_CONDA_NAME_LOOKUP)
        res.raise_for_status()
        PYPI_LOOKUP = yaml.safe_load(res.content)
    return PYPI_LOOKUP


def join_version_components(pieces):
    return ".".join(str(p) for p in pieces)


def normalize_pypi_name(name: str) -> str:
    if name in get_lookup():
        return get_lookup()[name]["conda_forge"]
    else:
        return name


def poetry_version_to_conda_version(version_string):
    components = [c.replace(" ", "").strip() for c in version_string.split(",")]
    output_components = []
    for c in components:
        version_pieces = c.lstrip("<>=^~!").split(".")
        if c[0] == "^":
            upper_version = version_pieces.copy()
            upper_version[0] = int(upper_version[0]) + 1
            for i in range(1, len(upper_version)):
                upper_version[i] = 0

            output_components.append(f">={join_version_components(version_pieces)}")
            output_components.append(f"<{join_version_components(upper_version)}")
        elif c[0] == "~":
            upper_version = version_pieces.copy()
            upper_version[1] = int(upper_version[1]) + 1
            for i in range(2, len(upper_version)):
                upper_version[i] = 0

            output_components.append(f">={join_version_components(version_pieces)}")
            output_components.append(f"<{join_version_components(upper_version)}")
        else:
            output_components.append(c.replace("===", "=").replace("==", "="))
    return ",".join(output_components)


def parse_poetry_pyproject_toml(
    pyproject_toml: pathlib.Path, platform: str
) -> LockSpecification:
    contents = toml.load(pyproject_toml)
    specs: List[str] = []
    for key in ["dependencies", "dev-dependencies"]:
        deps = contents.get("tool", {}).get("poetry", {}).get(key, {})
        for depname, depattrs in deps.items():
            conda_dep_name = normalize_pypi_name(depname)
            if isinstance(depattrs, collections.Mapping):
                poetry_version_spec = depattrs["version"]
                # TODO: support additional features such as markerts for things like sys_platform, platform_system
            elif isinstance(depattrs, str):
                poetry_version_spec = depattrs
            else:
                raise TypeError(
                    f"Unsupported type for dependency: {depname}: {depattrs:r}"
                )
            conda_version = poetry_version_to_conda_version(poetry_version_spec)

            if conda_version:
                spec = f"{conda_dep_name}[version{conda_version}]"
            else:
                spec = f"{conda_dep_name}"

            if conda_dep_name == "python":
                specs.insert(0, spec)
            else:
                specs.append(spec)

    channels = contents.get("tool", {}).get("conda-lock", {}).get("channels", [])

    return LockSpecification(specs=specs, channels=channels, platform=platform)
