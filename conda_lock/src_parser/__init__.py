import logging
import pathlib

from collections.abc import Sequence, Set
from typing import Optional

from conda_lock.common import ordered_union
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import Dependency, LockSpecification
from conda_lock.models.pip_repository import PipRepository
from conda_lock.src_parser.aggregation import aggregate_lock_specs
from conda_lock.src_parser.environment_yaml import (
    parse_environment_file,
    parse_platforms_from_env_file,
)
from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
from conda_lock.src_parser.pyproject_toml import (
    parse_platforms_from_pyproject_toml,
    parse_pyproject_toml,
)


DEFAULT_PLATFORMS = ["linux-64", "osx-arm64", "osx-64", "win-64"]


logger = logging.getLogger(__name__)


def _parse_platforms_from_srcs(src_files: list[pathlib.Path]) -> list[str]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    """
    all_file_platforms: list[list[str]] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            continue
        elif src_file.name == "pyproject.toml":
            all_file_platforms.append(parse_platforms_from_pyproject_toml(src_file))
        else:
            all_file_platforms.append(parse_platforms_from_env_file(src_file))

    return ordered_union(all_file_platforms)


def _parse_source_files(
    src_files: list[pathlib.Path], *, platforms: list[str], mapping_url: str
) -> list[LockSpecification]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    platforms :
        Target platforms to render environment.yaml and meta.yaml files for
    """
    desired_envs: list[LockSpecification] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(parse_meta_yaml_file(src_file, platforms=platforms))
        elif src_file.name == "pyproject.toml":
            desired_envs.append(
                parse_pyproject_toml(
                    src_file, platforms=platforms, mapping_url=mapping_url
                )
            )
        else:
            desired_envs.append(
                parse_environment_file(
                    src_file, platforms=platforms, mapping_url=mapping_url
                )
            )
    return desired_envs


def make_lock_spec(
    *,
    src_files: list[pathlib.Path],
    channel_overrides: Optional[Sequence[str]] = None,
    pip_repository_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    filtered_categories: Optional[Set[str]] = None,
    mapping_url: str,
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files.  If filtered_categories is set filter out specs that do not match those"""
    platforms = (
        list(platform_overrides)
        if platform_overrides
        else _parse_platforms_from_srcs(src_files)
    ) or DEFAULT_PLATFORMS

    lock_specs = _parse_source_files(
        src_files, platforms=platforms, mapping_url=mapping_url
    )

    aggregated_lock_spec = aggregate_lock_specs(lock_specs, platforms)

    # Use channel overrides if given, otherwise use the channels specified in the
    # source files.
    channels = (
        [Channel.from_string(co) for co in channel_overrides]
        if channel_overrides
        else aggregated_lock_spec.channels
    )

    pip_repositories = (
        [
            PipRepository.from_string(repo_override)
            for repo_override in pip_repository_overrides
        ]
        if pip_repository_overrides
        else aggregated_lock_spec.pip_repositories
    )

    if filtered_categories is None:
        dependencies = aggregated_lock_spec.dependencies
    else:
        # Filtering based on category (e.g. "main" or "dev") was requested.
        # Thus we need to filter the specs based on the category.
        def dep_has_category(d: Dependency, categories: Set[str]) -> bool:
            return d.category in categories

        dependencies = {
            platform: [
                d
                for d in dependencies
                if dep_has_category(d, categories=filtered_categories)
            ]
            for platform, dependencies in aggregated_lock_spec.dependencies.items()
        }

    return LockSpecification(
        dependencies=dependencies,
        channels=channels,
        pip_repositories=pip_repositories,
        sources=aggregated_lock_spec.sources,
        allow_pypi_requests=aggregated_lock_spec.allow_pypi_requests,
    )
