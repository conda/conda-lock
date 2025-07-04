import pathlib
import re
import sys

import yaml

from conda_lock.models.lock_spec import Dependency, LockSpecification
from conda_lock.src_parser.conda_common import conda_spec_to_versioned_dep
from conda_lock.src_parser.markers import evaluate_marker
from conda_lock.src_parser.selectors import filter_platform_selectors

from .pyproject_toml import parse_python_requirement


_whitespace = re.compile(r"\s+")
_conda_package_pattern = re.compile(r"^(?P<name>[A-Za-z0-9_-]+)\s?(?P<version>.*)?$")


def parse_conda_requirement(req: str) -> tuple[str, str]:
    match = _conda_package_pattern.match(req)
    if match:
        return match.group("name"), _whitespace.sub("", match.group("version"))
    else:
        raise ValueError(f"Can't parse conda spec from '{req}'")


def _parse_environment_file_for_platform(
    content: str,
    *,
    category: str,
    platform: str,
    mapping_url: str,
) -> list[Dependency]:
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

    # Split out any sub spec sections from the dependencies mapping
    mapping_specs = [x for x in specs if not isinstance(x, str)]
    specs = [x for x in specs if isinstance(x, str)]

    dependencies: list[Dependency] = []
    for spec in specs:
        dependencies.append(conda_spec_to_versioned_dep(spec, category))

    for mapping_spec in mapping_specs:
        pip = mapping_spec.get("pip")
        if pip is None:
            # might not be present OR might be None due to platform selector
            continue
        for spec in pip:
            if re.match(r"^-e .*$", spec):
                print(
                    (
                        f"Warning: editable pip dep '{spec}' will not be included in the lock file. "
                        "You will need to install it separately."
                    ),
                    file=sys.stderr,
                )
                continue

            dependency = parse_python_requirement(
                spec,
                manager="pip",
                category=category,
                mapping_url=mapping_url,
            )
            if evaluate_marker(dependency.markers, platform):
                # The above condition will skip adding the dependency if a
                # marker specifies a platform that doesn't match the target,
                # e.g. sys_platform == 'win32' for a linux target.
                dependencies.append(dependency)

        # ensure pip is in target env
        dependencies.append(
            parse_python_requirement("pip", manager="conda", mapping_url=mapping_url)
        )

    return dependencies


def parse_platforms_from_env_file(environment_file: pathlib.Path) -> list[str]:
    """
    Parse the list of platforms from an environment-yaml file
    """
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")

    with environment_file.open("r") as fo:
        content = fo.read()
        env_yaml_data = yaml.safe_load(content)

    return env_yaml_data.get("platforms", [])


def parse_environment_file(
    environment_file: pathlib.Path,
    platforms: list[str],
    mapping_url: str,
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
    channels: list[str] = env_yaml_data.get("channels", [])
    try:
        # conda-lock will use `--override-channels` so nodefaults is redundant.
        channels.remove("nodefaults")
    except ValueError:
        pass

    pip_repositories: list[str] = env_yaml_data.get("pip-repositories", [])

    # These extension fields are nonstandard
    category: str = env_yaml_data.get("category") or "main"

    # Parse with selectors for each target platform
    dep_map = {
        platform: _parse_environment_file_for_platform(
            content, category=category, platform=platform, mapping_url=mapping_url
        )
        for platform in platforms
    }

    return LockSpecification(
        dependencies=dep_map,
        channels=channels,  # type: ignore
        pip_repositories=pip_repositories,  # type: ignore
        sources=[environment_file],
    )
