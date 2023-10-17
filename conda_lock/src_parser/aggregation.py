import logging

from itertools import chain
from typing import Dict, List, Tuple, TypeVar

from conda_lock.common import ordered_union
from conda_lock.errors import ChannelAggregationError
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import Dependency, LockSpecification
from conda_lock.models.pip_repository import PipRepository


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
        channels = unify_package_sources(
            [lock_spec.channels for lock_spec in lock_specs]
        )
    except ValueError as e:
        raise ChannelAggregationError(*e.args)

    try:
        # For discussion see
        # <https://github.com/conda/conda-lock/pull/529#issuecomment-1766060611>
        pip_repositories = unify_package_sources(
            [lock_spec.pip_repositories for lock_spec in lock_specs]
        )
    except ValueError as e:
        raise ChannelAggregationError(*e.args)

    return LockSpecification(
        dependencies=dependencies,
        # Ensure channel are correctly ordered
        channels=channels,
        pip_repositories=pip_repositories,
        # uniquify metadata, preserving order
        sources=ordered_union(lock_spec.sources for lock_spec in lock_specs),
        allow_pypi_requests=all(
            lock_spec.allow_pypi_requests for lock_spec in lock_specs
        ),
    )


PackageSource = TypeVar("PackageSource", Channel, PipRepository)


def unify_package_sources(
    collections: List[List[PackageSource]],
) -> List[PackageSource]:
    """Unify the package sources from multiple lock specs.

    To be able to merge the lock specs, the package sources must be compatible between
    them. This means that between any two lock specs, the package sources must be
    identical or one must be an extension of the other.

    This allows us to use a superset of all of the package source lists in the
    aggregated lock spec.

    The following is allowed:

    > unify_package_sources([[channel_two, channel_one], [channel_one]])
    [channel_two, channel_one]

    Whilst the following will fail:

    > unify_package_sources([[channel_two, channel_one], [channel_three, channel_one]])

    In the failing example, it is not possible to predictably decide which channel
    to search first, `channel_two` or `channel_three`, so we error in this case.
    """
    if not collections:
        return []
    result = max(collections, key=len)
    for collection in collections:
        if collection == []:
            truncated_result = []
        else:
            truncated_result = result[-len(collection) :]
        if collection != truncated_result:
            raise ValueError(
                f"{collection} is not an ordered subset at the end of {result}"
            )
    return result
