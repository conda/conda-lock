import logging

from itertools import chain
from typing import Dict, List, Tuple

from conda_lock.common import ordered_union, suffix_union
from conda_lock.errors import ChannelAggregationError
from conda_lock.models.lock_spec import Dependency, LockSpecification


logger = logging.getLogger(__name__)


def aggregate_lock_specs(
    lock_specs: List[LockSpecification],
    platforms: List[str],
) -> LockSpecification:
    for lock_spec in lock_specs:
        if set(lock_spec.platforms) != set(platforms):
            raise ValueError(
                f"Lock specifications must have the same platforms in order to be "
                f"aggregated. Expected platforms are {set(platforms)}, but the lock "
                f"specification from {[str(s) for s in lock_spec.sources]} has "
                f"platforms {set(lock_spec.platforms)}."
            )

    dependencies: Dict[str, List[Dependency]] = {}
    for platform in platforms:
        # unique dependencies
        unique_deps: Dict[Tuple[str, str], Dependency] = {}
        for dep in chain.from_iterable(
            lock_spec.dependencies.get(platform, []) for lock_spec in lock_specs
        ):
            key = (dep.manager, dep.name)
            unique_deps[key] = dep

        dependencies[platform] = list(unique_deps.values())

    try:
        channels = suffix_union(lock_spec.channels for lock_spec in lock_specs)
    except ValueError as e:
        raise ChannelAggregationError(*e.args)

    return LockSpecification(
        dependencies=dependencies,
        # Ensure channel are correctly ordered
        channels=channels,
        # uniquify metadata, preserving order
        sources=ordered_union(lock_spec.sources for lock_spec in lock_specs),
        allow_pypi_requests=all(
            lock_spec.allow_pypi_requests for lock_spec in lock_specs
        ),
    )
