import logging
import pathlib

from typing import AbstractSet, List, Optional, Sequence

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
from conda_lock.virtual_package import FakeRepoData


DEFAULT_PLATFORMS = ["linux-64", "osx-arm64", "osx-64", "win-64"]


logger = logging.getLogger(__name__)


def _parse_platforms_from_srcs(src_files: List[pathlib.Path]) -> List[str]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    """
    all_file_platforms: List[List[str]] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            continue
        elif src_file.name == "pyproject.toml":
            all_file_platforms.append(parse_platforms_from_pyproject_toml(src_file))
        else:
            all_file_platforms.append(parse_platforms_from_env_file(src_file))

    return ordered_union(all_file_platforms)


def _parse_source_files(
    src_files: List[pathlib.Path],
    platforms: List[str],
) -> List[LockSpecification]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    platforms :
        Target platforms to render environment.yaml and meta.yaml files for
    """
    desired_envs: List[LockSpecification] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(parse_meta_yaml_file(src_file, platforms))
        elif src_file.name == "pyproject.toml":
            desired_envs.append(parse_pyproject_toml(src_file, platforms))
        else:
            desired_envs.append(parse_environment_file(src_file, platforms))
    return desired_envs


def make_lock_spec(
    *,
    src_files: List[pathlib.Path],
    virtual_package_repo: FakeRepoData,
    channel_overrides: Optional[Sequence[str]] = None,
    pip_repository_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    required_categories: Optional[AbstractSet[str]] = None,
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files.  If required_categories is set filter out specs that do not match those"""
    platforms = (
        list(platform_overrides)
        if platform_overrides
        else _parse_platforms_from_srcs(src_files)
    ) or DEFAULT_PLATFORMS

    lock_specs = _parse_source_files(src_files, platforms)

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

    if required_categories is None:
        dependencies = aggregated_lock_spec.dependencies
    else:
        # Filtering based on category (e.g. "main" or "dev") was requested.
        # Thus we need to filter the specs based on the category.
        def dep_has_category(d: Dependency, categories: AbstractSet[str]) -> bool:
            return d.category in categories

        dependencies = {
            platform: [
                d
                for d in dependencies
                if dep_has_category(d, categories=required_categories)
            ]
            for platform, dependencies in aggregated_lock_spec.dependencies.items()
        }

    return LockSpecification(
        dependencies=dependencies,
        channels=channels,
        pip_repositories=pip_repositories,
        sources=aggregated_lock_spec.sources,
        virtual_package_repo=virtual_package_repo,
        allow_pypi_requests=aggregated_lock_spec.allow_pypi_requests,
    )
