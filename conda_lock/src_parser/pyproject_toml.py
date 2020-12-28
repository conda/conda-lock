import collections
import collections.abc
import pathlib

from typing import List, Mapping, Optional

import requests
import toml
import yaml

from conda_lock.common import get_in
from conda_lock.src_parser import LockSpecification


# TODO: make this configurable
PYPI_TO_CONDA_NAME_LOOKUP = "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/mappings/pypi/grayskull_pypi_mapping.yaml"
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
        lookup = get_lookup()[name]
        return lookup.get("conda_name") or lookup.get("conda_forge")
    else:
        return name


def poetry_version_to_conda_version(version_string):
    components = [c.replace(" ", "").strip() for c in version_string.split(",")]
    output_components = []

    for c in components:
        if len(c) == 0:
            continue
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
    pyproject_toml: pathlib.Path, platform: str, include_dev_dependencies: bool
) -> LockSpecification:
    contents = toml.load(pyproject_toml)
    specs: List[str] = []
    dependency_sections = ["dependencies"]
    if include_dev_dependencies:
        dependency_sections.append("dev-dependencies")

    for key in dependency_sections:
        deps = get_in(["tool", "poetry", key], contents, {})
        for depname, depattrs in deps.items():
            conda_dep_name = normalize_pypi_name(depname)
            if isinstance(depattrs, collections.Mapping):
                poetry_version_spec = depattrs["version"]
                # TODO: support additional features such as markers for things like sys_platform, platform_system
            elif isinstance(depattrs, str):
                poetry_version_spec = depattrs
            else:
                raise TypeError(
                    f"Unsupported type for dependency: {depname}: {depattrs:r}"
                )
            conda_version = poetry_version_to_conda_version(poetry_version_spec)
            spec = to_match_spec(conda_dep_name, conda_version)

            if conda_dep_name == "python":
                specs.insert(0, spec)
            else:
                specs.append(spec)

    conda_deps = get_in(["tool", "conda-lock", "dependencies"], contents, {})
    specs.extend(parse_conda_dependencies(conda_deps))

    channels = get_in(["tool", "conda-lock", "channels"], contents, [])

    return LockSpecification(specs=specs, channels=channels, platform=platform)


def to_match_spec(conda_dep_name, conda_version):
    if conda_version:
        spec = f"{conda_dep_name}[version='{conda_version}']"
    else:
        spec = f"{conda_dep_name}"
    return spec


def parse_pyproject_toml(
    pyproject_toml: pathlib.Path, platform: str, include_dev_dependencies: bool
):
    contents = toml.load(pyproject_toml)
    build_system = get_in(["build-system", "build-backend"], contents)
    parse = parse_poetry_pyproject_toml
    if build_system.startswith("poetry"):
        parse = parse_poetry_pyproject_toml
    elif build_system.startswith("flit"):
        parse = parse_flit_pyproject_toml
    else:
        import warnings

        warnings.warn(
            "Could not detect build-system in pyproject.toml.  Assuming poetry"
        )

    return parse(pyproject_toml, platform, include_dev_dependencies)


def python_requirement_to_conda_spec(requirement: str):
    """Parse a requirements.txt like requirement to a conda spec"""
    requirement_specifier = requirement.split(";")[0].strip()
    from pkg_resources import Requirement

    parsed_req = Requirement.parse(requirement_specifier)
    name = parsed_req.unsafe_name
    collapsed_version = ",".join("".join(spec) for spec in parsed_req.specs)
    conda_version = poetry_version_to_conda_version(collapsed_version)

    conda_dep_name = normalize_pypi_name(name)
    return to_match_spec(conda_dep_name, conda_version)


def parse_flit_pyproject_toml(
    pyproject_toml: pathlib.Path, platform: str, include_dev_dependencies: bool
):
    contents = toml.load(pyproject_toml)

    requirements = get_in(["tool", "flit", "metadata", "requires"], contents, [])
    if include_dev_dependencies:
        requirements += get_in(
            ["tool", "flit", "metadata", "requires-extra", "test"], contents, []
        )
        requirements += get_in(
            ["tool", "flit", "metadata", "requires-extra", "dev"], contents, []
        )

    dependency_sections = ["tool"]
    if include_dev_dependencies:
        dependency_sections += ["dev-dependencies"]

    specs = [python_requirement_to_conda_spec(req) for req in requirements]

    conda_deps = get_in(["tool", "conda-lock", "dependencies"], contents, {})
    specs.extend(parse_conda_dependencies(conda_deps))

    channels = get_in(["tool", "conda-lock", "channels"], contents, [])

    return LockSpecification(specs=specs, channels=channels, platform=platform)


def parse_conda_dependencies(conda_deps: Mapping) -> List[str]:
    specs = []
    for depname, depattrs in conda_deps.items():
        if isinstance(depattrs, str):
            conda_version = depattrs
        else:
            raise TypeError(f"Unsupported type for dependency: {depname}: {depattrs:r}")
        specs.append(to_match_spec(depname, conda_version))
    return specs
