import pathlib
import re
import sys

from typing import List, Set

from ruamel.yaml import YAML

from conda_lock.src_parser import SourceDependency, SourceFile
from conda_lock.src_parser.conda_common import conda_spec_to_versioned_dep
from conda_lock.src_parser.selectors import parse_selector_comment_for_dep

from .pyproject_toml import parse_python_requirement


def parse_environment_file(
    environment_file: pathlib.Path,
    *,
    pip_support: bool = False,
) -> SourceFile:
    """
    Parse an simple environment-yaml file in a platform and version independent way.
    """
    if not environment_file.exists():
        raise FileNotFoundError(f"Environment File {environment_file} not found")
    env_yaml_data = YAML().load(environment_file)

    # Get any (nonstandard) given values in the environment file
    platforms: Set[str] = set(env_yaml_data.get("platforms", []))
    category: str = str(env_yaml_data.get("category", "main"))
    channels: List[str] = env_yaml_data.get("channels", []).copy()

    all_specs = env_yaml_data["dependencies"]
    specs = [x for x in all_specs if isinstance(x, str)]
    mapping_specs = [x for x in all_specs if not isinstance(x, str)]

    # Get and Parse Dependencies
    dependencies: List[SourceDependency] = []
    for idx, spec in enumerate(specs):
        sdep = conda_spec_to_versioned_dep(spec, category)
        sdep.selectors.platform = parse_selector_comment_for_dep(all_specs.ca, idx)
        dependencies.append(sdep)

    for mapping_spec in mapping_specs:
        if "pip" not in mapping_spec:
            continue

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

    return SourceFile(
        file=environment_file,
        dependencies=dependencies,
        channels=channels,  # type: ignore
        platforms=platforms,
    )
