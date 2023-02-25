import logging
import pathlib

from typing import AbstractSet, List, Optional, Sequence

from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import Dependency, LockSpecification
from conda_lock.src_parser.aggregation import aggregate_lock_specs
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml
from conda_lock.virtual_package import FakeRepoData


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]


logger = logging.getLogger(__name__)


def _parse_source_files(
    src_files: List[pathlib.Path],
    platform_overrides: Optional[Sequence[str]],
) -> List[LockSpecification]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    platform_overrides :
        Target platforms to render environment.yaml and meta.yaml files for
    """
    desired_envs: List[LockSpecification] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(
                parse_meta_yaml_file(
                    src_file, list(platform_overrides or DEFAULT_PLATFORMS)
                )
            )
        elif src_file.name == "pyproject.toml":
            desired_envs.append(parse_pyproject_toml(src_file))
        else:
            desired_envs.append(
                parse_environment_file(
                    src_file,
                    platform_overrides,
                    default_platforms=DEFAULT_PLATFORMS,
                )
            )
    return desired_envs


def make_lock_spec(
    *,
    src_files: List[pathlib.Path],
    virtual_package_repo: FakeRepoData,
    channel_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    required_categories: Optional[AbstractSet[str]] = None,
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files.  If required_categories is set filter out specs that do not match those"""
    lock_specs = _parse_source_files(
        src_files=src_files, platform_overrides=platform_overrides
    )

    lock_spec = aggregate_lock_specs(lock_specs)
    lock_spec.virtual_package_repo = virtual_package_repo
    lock_spec.channels = (
        [Channel.from_string(co) for co in channel_overrides]
        if channel_overrides
        else lock_spec.channels
    )
    lock_spec.platforms = (
        list(platform_overrides) if platform_overrides else lock_spec.platforms
    ) or list(DEFAULT_PLATFORMS)

    if required_categories is not None:

        def dep_has_category(d: Dependency, categories: AbstractSet[str]) -> bool:
            return d.category in categories

        lock_spec.dependencies = [
            d
            for d in lock_spec.dependencies
            if dep_has_category(d, categories=required_categories)
        ]

    return lock_spec
