from collections import defaultdict
from typing import Dict, List, NamedTuple, Tuple

from conda_lock.models.lock_spec import Dependency


class FullDepKey(NamedTuple):
    """A unique key for a dependency in a LockSpecification."""

    name: str
    category: str
    platform: str
    manager: str

    def drop_platform(self) -> "DepKeyNoPlatform":
        return DepKeyNoPlatform(
            name=self.name, category=self.category, manager=self.manager
        )


class DepKeyNoPlatform(NamedTuple):
    """A key for a dependency in a LockSpecification without a platform."""

    name: str
    category: str
    manager: str


def aggregate_platform_independent_deps(
    dependencies: Dict[str, List[Dependency]],
) -> Tuple[Dict[DepKeyNoPlatform, Dependency], Dict[FullDepKey, Dependency]]:
    """Aggregate platform-independent dependencies.

    >>> numpy1a = VersionedDependency(name="numpy", version="1.2.3")
    >>> numpy1b = VersionedDependency(name="numpy", version="1.2.3")
    >>> pandas1 = VersionedDependency(name="pandas", version="4.5.6")
    >>> pandas2 = VersionedDependency(name="pandas", version="7.8.9")
    >>> xarray = VersionedDependency(name="xarray", version="1.2.3")
    >>> dependencies = {
    ...     "linux-64": [numpy1a, pandas1],
    ...     "osx-64": [numpy1b, pandas1, xarray],
    ...     "win-64": [numpy1a, pandas2],
    ... }
    >>> platform_independent, platform_specific = aggregate_platform_independent_deps(
    ...     dependencies
    ... )

    Since `numpy1a` and `numpy1b` are equal, `numpy` is platform-independent.

    >>> platform_independent  # doctest: +NORMALIZE_WHITESPACE
    {DepKeyNoPlatform(name='numpy', category='main', manager='conda'):
      VersionedDependency(name='numpy', manager='conda', category='main', extras=[],
        markers=None, version='1.2.3', build=None, conda_channel=None, hash=None)}

    `xarray` only appears on `osx-64`.
    `pandas` is present on all platforms, but the versions aren't all the same.

    >>> platform_specific  # doctest: +NORMALIZE_WHITESPACE
    {FullDepKey(name='pandas', category='main', platform='linux-64', manager='conda'):
       VersionedDependency(name='pandas', manager='conda', category='main', extras=[],
         markers=None, version='4.5.6', build=None, conda_channel=None, hash=None),
     FullDepKey(name='pandas', category='main', platform='osx-64', manager='conda'):
       VersionedDependency(name='pandas', manager='conda', category='main', extras=[],
         markers=None, version='4.5.6', build=None, conda_channel=None, hash=None),
     FullDepKey(name='xarray', category='main', platform='osx-64', manager='conda'):
       VersionedDependency(name='xarray', manager='conda', category='main', extras=[],
         markers=None, version='1.2.3', build=None, conda_channel=None, hash=None),
     FullDepKey(name='pandas', category='main', platform='win-64', manager='conda'):
       VersionedDependency(name='pandas', manager='conda', category='main', extras=[],
         markers=None, version='7.8.9', build=None, conda_channel=None, hash=None)}
    """
    indexed_deps: Dict[FullDepKey, Dependency] = {}
    for platform, deps in dependencies.items():
        for dep in deps:
            key = FullDepKey(
                name=dep.name,
                category=dep.category,
                platform=platform,
                manager=dep.manager,
            )
            if key in indexed_deps:
                raise ValueError(
                    f"Duplicate dependency {key}: {dep}, {indexed_deps[key]}"
                )
            indexed_deps[key] = dep

    # Collect by platform
    collected_deps: Dict[DepKeyNoPlatform, List[Dependency]] = defaultdict(list)
    for key, dep in indexed_deps.items():
        collected_deps[key.drop_platform()].append(dep)

    # Check for platform-independent dependencies
    num_platforms = len(dependencies.keys())
    platform_independent_deps: Dict[DepKeyNoPlatform, Dependency] = {
        np_key: deps[0]
        for np_key, deps in collected_deps.items()
        # It's independent if there's a dep for each platform and they're all the same.
        if len(deps) == num_platforms
        and all(curr == next for curr, next in zip(deps, deps[1:]))
    }
    platform_specific_deps: Dict[FullDepKey, Dependency] = {
        key: dep
        for key, dep in indexed_deps.items()
        if key.drop_platform() not in platform_independent_deps
    }
    return platform_independent_deps, platform_specific_deps
