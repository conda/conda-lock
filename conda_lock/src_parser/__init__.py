import logging
import pathlib

from itertools import chain
from typing import AbstractSet, Dict, List, Optional, Sequence, Tuple

from conda_lock.common import ordered_union, suffix_union
from conda_lock.errors import ChannelAggregationError
from conda_lock.models.channel import Channel
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
from conda_lock.src_parser.models import (
    Dependency,
    LockSpecification,
    Selectors,
    URLDependency,
    VersionedDependency,
)
from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml
from conda_lock.virtual_package import FakeRepoData


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]


logger = logging.getLogger(__name__)


def aggregate_lock_specs(
    lock_specs: List[LockSpecification],
) -> LockSpecification:
    # unique dependencies
    unique_deps: Dict[Tuple[str, str], Dependency] = {}
    for dep in chain.from_iterable(
        [lock_spec.dependencies for lock_spec in lock_specs]
    ):
        key = (dep.manager, dep.name)
        if key in unique_deps:
            # Override existing, but merge selectors
            previous_selectors = unique_deps[key].selectors
            previous_selectors |= dep.selectors
            dep.selectors = previous_selectors
        unique_deps[key] = dep

    dependencies = list(unique_deps.values())
    try:
        channels = suffix_union(lock_spec.channels or [] for lock_spec in lock_specs)
    except ValueError as e:
        raise ChannelAggregationError(*e.args)

    return LockSpecification(
        dependencies=dependencies,
        # Ensure channel are correctly ordered
        channels=channels,
        # uniquify metadata, preserving order
        platforms=ordered_union(lock_spec.platforms or [] for lock_spec in lock_specs),
        sources=ordered_union(lock_spec.sources or [] for lock_spec in lock_specs),
    )


def parse_source_files(
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
    lock_specs = parse_source_files(
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


__all__ = [
    "Dependency",
    "LockSpecification",
    "Selectors",
    "URLDependency",
    "VersionedDependency",
    "parse_environment_file",
    "parse_meta_yaml_file",
    "parse_pyproject_toml",
    "DEFAULT_PLATFORMS",
    "aggregate_lock_specs",
    "parse_source_files",
    "make_lock_spec",
]
