import logging
import pathlib

from itertools import chain
from typing import Dict, List, Optional, Sequence, Tuple

from conda_lock.common import ordered_union, suffix_union
from conda_lock.errors import ChannelAggregationError

from .environment_yaml import parse_environment_file
from .meta_yaml import parse_meta_yaml_file
from .models import Dependency, LockSpecification
from .models import Selectors as Selectors
from .models import URLDependency as URLDependency
from .models import VersionedDependency as VersionedDependency
from .pyproject_toml import parse_pyproject_toml


# TODO: Duplicate, remove in next commit
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
