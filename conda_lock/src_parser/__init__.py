import logging

from itertools import chain
from typing import Dict, List, Tuple

from conda_lock.common import ordered_union, suffix_union
from conda_lock.errors import ChannelAggregationError

from .models import Dependency, LockSpecification
from .models import Selectors as Selectors
from .models import URLDependency as URLDependency
from .models import VersionedDependency as VersionedDependency


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
