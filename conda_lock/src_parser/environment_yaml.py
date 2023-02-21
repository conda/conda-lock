import pathlib
import re
import sys

from typing import List, Optional, Sequence, Tuple

import yaml

from conda_lock.models.lock_spec import Dependency, LockSpecification
from conda_lock.src_parser.aggregation import aggregate_lock_specs
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


def _parse_environment_file_for_platform(
    environment_file: pathlib.Path,
    content: str,
    platform: str,
) -> LockSpecification:
    """
    Parse dependencies from a conda environment specification for an
    assumed target platform.

    Parameters
    ----------
    environment_file :
        Path to environment.yml
    platform :
        Target platform to use when parsing selectors to filter lines
    """
    filtered_content = "\n".join(filter_platform_selectors(content, platform=platform))
    env_yaml_data = yaml.safe_load(filtered_content)

    specs = env_yaml_data["dependencies"]
    channels: List[str] = env_yaml_data.get("channels", [])

    # These extension fields are nonstandard
    platforms: List[str] = env_yaml_data.get("platforms", [])
    category: str = env_yaml_data.get("category") or "main"

    # Split out any sub spec sections from the dependencies mapping
    mapping_specs = [x for x in specs if not isinstance(x, str)]
    specs = [x for x in specs if isinstance(x, str)]

    dependencies: List[Dependency] = []
    for spec in specs:
        vdep = conda_spec_to_versioned_dep(spec, category)
        vdep.selectors.platform = [platform]
        dependencies.append(vdep)

    for mapping_spec in mapping_specs:
        if "pip" in mapping_spec:
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

    return LockSpecification(
        dependencies=dependencies,
        channels=channels,  # type: ignore
        platforms=platforms,
        sources=[environment_file],
    )


def parse_environment_file(
    environment_file: pathlib.Path,
    given_platforms: Optional[Sequence[str]],
    *,
    default_platforms: List[str] = [],
) -> LockSpecification:
    """Parse a simple environment-yaml file for dependencies assuming the target platforms.

    * This will emit one dependency set per target platform. These may differ
      if the dependencies depend on platform selectors.
    * This does not support multi-output files and will ignore all lines with
      selectors other than platform.
    """
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")

    with environment_file.open("r") as fo:
        content = fo.read()
        env_yaml_data = yaml.safe_load(content)

    # Get list of platforms from the input file
    yaml_platforms: Optional[List[str]] = env_yaml_data.get("platforms")
    # Final list of platforms is the following order of priority
    # 1) List Passed in via the -p flag (if any given)
    # 2) List From the YAML File (if specified)
    # 3) Default List of Platforms to Render
    platforms = list(given_platforms or yaml_platforms or default_platforms)

    # Parse with selectors for each target platform
    spec = aggregate_lock_specs(
        [
            _parse_environment_file_for_platform(
                environment_file,
                content,
                platform,
            )
            for platform in platforms
        ]
    )

    # Remove platform selectors if they apply to all targets
    for dep in spec.dependencies:
        if dep.selectors.platform == platforms:
            dep.selectors.platform = None

    # Use the list of rendered platforms for the output spec only if
    # there is a dependency that is not used on all platforms.
    # This is unlike meta.yaml because environment-yaml files can contain an
    # internal list of platforms, which should be used as long as it
    spec.platforms = platforms
    return spec
