import logging

from collections.abc import Set
from itertools import chain
from typing import TypeVar

from conda_lock.common import ordered_union
from conda_lock.errors import ChannelAggregationError
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import (
    Dependency,
    LockSpecification,
    VersionedDependency,
)
from conda_lock.models.pip_repository import PipRepository


logger = logging.getLogger(__name__)


def aggregate_lock_specs(
    lock_specs: list[LockSpecification],
    platforms: list[str],
    *,
    filtered_categories: Set[str] | None = None,
) -> LockSpecification:
    for lock_spec in lock_specs:
        if set(lock_spec.platforms) != set(platforms):
            raise ValueError(
                f"Lock specifications must have the same platforms in order to be "
                f"aggregated. Expected platforms are {set(platforms)}, but the lock "
                f"specification from {[str(s) for s in lock_spec.sources]} has "
                f"platforms {set(lock_spec.platforms)}."
            )

    dependencies: dict[str, list[Dependency]] = {}
    for platform in platforms:
        # unique dependencies
        unique_deps: dict[tuple[str, str], Dependency] = {}
        # Conda pip is special: every `pip:` subsection generates an unconstrained
        # fallback requirement in the main category, because the subsection needs
        # pip wherever it is installed. A real explicit constraint beats the
        # fallback wherever either appears. Between the fallback and an
        # unconstrained explicit requirement, ordinary last-wins still decides
        # which one is written out, so their representations are unchanged. In
        # both cases pip keeps the fallback's main membership. Only the final
        # explicit requirement and the final fallback decide; anything either of
        # them superseded is irrelevant.
        explicit_pip: Dependency | None = None
        fallback_pip: VersionedDependency | None = None
        for dep in chain.from_iterable(
            lock_spec.dependencies.get(platform, []) for lock_spec in lock_specs
        ):
            key = (dep.manager, dep.name)
            if (
                isinstance(dep, VersionedDependency)
                and dep.manager == "conda"
                and dep.is_pip_fallback
            ):
                if fallback_pip is None:
                    # The fallback identifies pip's mapped conda name. Recover
                    # the last explicit requirement that preceded it, if any.
                    explicit_pip = unique_deps.get(key)
                # Provenance is resolved here and never leaves the aggregate.
                dep = fallback_pip = dep.model_copy(update={"is_pip_fallback": False})
            elif fallback_pip is not None and key == ("conda", fallback_pip.name):
                # Whatever the source wrote last, including a URL, VCS or
                # path reference, is the explicit requirement.
                explicit_pip = dep
            unique_deps[key] = dep

        # The explicit requirement is written out when it is a real constraint,
        # wherever it appears, or when it came after every fallback.
        explicit_winner: Dependency | None = None
        if explicit_pip is not None and fallback_pip is not None:
            pip_key = ("conda", fallback_pip.name)
            if not _is_unconstrained(explicit_pip):
                explicit_winner = unique_deps[pip_key] = explicit_pip
            elif unique_deps[pip_key] is explicit_pip:
                explicit_winner = explicit_pip

        # Preserve last-wins handling of explicit dependencies, then filter categories.
        if filtered_categories is not None:
            unique_deps = {
                key: dep
                for key, dep in unique_deps.items()
                if dep.category in filtered_categories
            }
        if (
            explicit_winner is not None
            and fallback_pip is not None
            and (
                filtered_categories is None
                or fallback_pip.category in filtered_categories
            )
        ):
            pip_key = ("conda", fallback_pip.name)
            if pip_key not in unique_deps:
                # The explicit requirement belongs to an excluded category, so
                # the subsection's own requirement applies again.
                unique_deps[pip_key] = fallback_pip
            elif explicit_winner.category != fallback_pip.category:
                # pip is needed by the subsection as well as by the explicit
                # requirement. The fallback's main category is installed with
                # every selection, so it covers both without merging anything.
                unique_deps[pip_key] = explicit_winner.model_copy(
                    update={"category": fallback_pip.category}
                )

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


def _is_unconstrained(dep: Dependency) -> bool:
    return (
        isinstance(dep, VersionedDependency)
        and dep.version in ("", "*")
        and dep.build in (None, "*")
        and dep.conda_channel is None
        and dep.hash is None
    )


PackageSource = TypeVar("PackageSource", Channel, PipRepository)


def unify_package_sources(
    collections: list[list[PackageSource]],
) -> list[PackageSource]:
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
