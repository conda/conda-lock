import pathlib
import re
import sys

from typing import List, Tuple

import yaml

from conda_lock.src_parser import Dependency, LockSpecification
from conda_lock.src_parser.conda_common import conda_spec_to_versioned_dep
from conda_lock.src_parser.selectors import filter_platform_selectors

from .pyproject_toml import parse_python_requirement


_whitespace = re.compile(r"\s+")
_conda_package_pattern = re.compile(r"^(?P<name>[A-Za-z0-9_-]+)\s?(?P<version>.*)?$")


def parse_conda_requirement(req: str) -> Tuple[str, str]:
    match = _conda_package_pattern.match(req)
    if match:
        return match.group("name"), _whitespace.sub("", match.group("version"))
    else:
        raise ValueError(f"Can't parse conda spec from '{req}'")


def parse_environment_file(
    environment_file: pathlib.Path, *, pip_support: bool = False
) -> LockSpecification:
    """
    Parse dependencies from a conda environment specification

    Parameters
    ----------
    environment_file :
        Path to environment.yml
    pip_support :
        Emit dependencies in pip section of environment.yml. If False, print a
        warning and ignore pip dependencies.

    """
    dependencies: List[Dependency] = []
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")

    with environment_file.open("r") as fo:
        content = fo.read()
        # TODO: improve this call since we *SHOULD* pass the right platform here
        filtered_content = "\n".join(filter_platform_selectors(content, platform=""))
        assert yaml.safe_load(filtered_content) == yaml.safe_load(
            content
        ), "selectors are temporarily gone"

        env_yaml_data = yaml.safe_load(filtered_content)
    specs = env_yaml_data["dependencies"]
    channels = env_yaml_data.get("channels", [])

    # These extension fields are nonstandard
    platforms = env_yaml_data.get("platforms", [])
    category = env_yaml_data.get("category") or "main"

    # Split out any sub spec sections from the dependencies mapping
    mapping_specs = [x for x in specs if not isinstance(x, str)]
    specs = [x for x in specs if isinstance(x, str)]

    for spec in specs:
        vdep = conda_spec_to_versioned_dep(spec, category)
        dependencies.append(vdep)
    for mapping_spec in mapping_specs:
        if "pip" in mapping_spec:
            if pip_support:
                for spec in mapping_spec["pip"]:
                    if re.match(r"^-e .*$", spec):
                        print(
                            (
                                f"Warning: editable pip dep '{spec}' will not be included in the lock file. "
                                "You will need to install it separately."
                            ),
                            file=sys.stderr,
                        )
                        continue

                    dependencies.append(
                        parse_python_requirement(
                            spec,
                            manager="pip",
                            optional=category != "main",
                            category=category,
                            normalize_name=False,
                        )
                    )

                # ensure pip is in target env
                dependencies.append(parse_python_requirement("pip", manager="conda"))
            else:
                print(
                    (
                        "Warning: found pip deps, but conda-lock was installed without pypi support. "
                        "pip dependencies will not be included in the lock file. Either install them "
                        "separately, or install conda-lock with `-E pip_support`."
                    ),
                    file=sys.stderr,
                )

    return LockSpecification(
        dependencies=dependencies,
        channels=channels,
        platforms=platforms,
        sources=[environment_file],
    )
