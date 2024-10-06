"""This module contains the `unify_platform_independent_deps` function.

This is deliberately placed in a separate module since it doesn't directly have to
do with the logic for exporting a lock specification, and the logic is a bit involved.
"""

from collections import defaultdict
from typing import Dict, List, NamedTuple, Optional, Union

from conda_lock.models.lock_spec import Dependency


class EditableDependency(NamedTuple):
    name: str
    path: str


class DepKey(NamedTuple):
    """A hashable key for a `Dependency` object and the platform under consideration.

    There are two main motivations for this class.

    1. A `Dependency` does not have its own `platform` attribute, but we need to
    categorize dependencies by platform. This class allows us to do that.

    2. A `Dependency` is a Pydantic model, so it isn't hashable and can't be used as a
    key in a dictionary. This class is hashable and can be used as a key, enabling
    us to index dependencies by their attributes.

    When `platform` is `None`, this signifies a platform-independent dependency.
    """

    name: str
    category: str
    platform: Optional[str]
    manager: str

    def drop_platform(self) -> "DepKey":
        return self._replace(platform=None)


def unify_platform_independent_deps(
    dependencies: Dict[str, List[Dependency]],
    *,
    editables: Optional[List[EditableDependency]] = None,
) -> Dict[DepKey, Union[Dependency, EditableDependency]]:
    """Combine identical dependencies for all platforms into a single dependency.

    Returns a tuple of two dictionaries:

    >>> from conda_lock.models.lock_spec import VersionedDependency
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

    Since `numpy1a` and `numpy1b` are equal, `numpy` is platform-independent.

    `xarray` only appears on `osx-64`.

    `pandas` is present on all platforms, but the versions aren't all the same.

    >>> unified = unify_platform_independent_deps(dependencies)
    >>> for key in unified:
    ...     print(key)
    DepKey(name='numpy', category='main', platform=None, manager='conda')
    DepKey(name='pandas', category='main', platform='linux-64', manager='conda')
    DepKey(name='pandas', category='main', platform='osx-64', manager='conda')
    DepKey(name='xarray', category='main', platform='osx-64', manager='conda')
    DepKey(name='pandas', category='main', platform='win-64', manager='conda')

    The full result:

    >>> unified  # doctest: +NORMALIZE_WHITESPACE
    {DepKey(name='numpy', category='main', platform=None, manager='conda'):
      VersionedDependency(name='numpy', manager='conda', category='main', extras=[],
        markers=None, version='1.2.3', build=None, conda_channel=None, hash=None),
     DepKey(name='pandas', category='main', platform='linux-64', manager='conda'):
      VersionedDependency(name='pandas', manager='conda', category='main', extras=[],
        markers=None, version='4.5.6', build=None, conda_channel=None, hash=None),
     DepKey(name='pandas', category='main', platform='osx-64', manager='conda'):
      VersionedDependency(name='pandas', manager='conda', category='main', extras=[],
        markers=None, version='4.5.6', build=None, conda_channel=None, hash=None),
     DepKey(name='xarray', category='main', platform='osx-64', manager='conda'):
      VersionedDependency(name='xarray', manager='conda', category='main', extras=[],
        markers=None, version='1.2.3', build=None, conda_channel=None, hash=None),
     DepKey(name='pandas', category='main', platform='win-64', manager='conda'):
      VersionedDependency(name='pandas', manager='conda', category='main', extras=[],
        markers=None, version='7.8.9', build=None, conda_channel=None, hash=None)}
    """
    indexed_deps: Dict[DepKey, Dependency] = {}
    for platform, deps in dependencies.items():
        for dep in deps:
            key = DepKey(
                name=dep.name,
                category=dep.category,
                platform=platform,
                manager=dep.manager,
            )
            if key in indexed_deps:
                raise ValueError(
                    f"Duplicate dependency {key}: {dep}, {indexed_deps[key]}"
                )
            # In the beginning each dep has a platform.
            assert key.platform is not None
            indexed_deps[key] = dep

    # Collect deps which differ only by platform
    collected_deps: Dict[DepKey, List[Dependency]] = defaultdict(list)
    for key, dep in indexed_deps.items():
        collected_deps[key.drop_platform()].append(dep)

    editable_deps: Dict[DepKey, EditableDependency] = {}
    for editable in editables or []:
        key = DepKey(name=editable.name, category="main", platform=None, manager="pip")
        if key in collected_deps:
            raise ValueError(
                f"Editable dependency {editable.name} conflicts with existing "
                f"dependency {collected_deps[key][0]}"
            )
        editable_deps[key] = editable

    # Check for platform-independent dependencies
    num_platforms = len(dependencies.keys())
    platform_independent_deps: Dict[DepKey, Dependency] = {
        np_key: deps[0]
        for np_key, deps in collected_deps.items()
        # It's independent if there's a dep for each platform and they're all the same.
        if len(deps) == num_platforms
        and all(curr == next for curr, next in zip(deps, deps[1:]))
    }
    assert all(key.platform is None for key in platform_independent_deps)

    # The platform-specific dependencies are now those not in platform_independent_deps.
    platform_specific_deps: Dict[DepKey, Dependency] = {
        key: dep
        for key, dep in indexed_deps.items()
        if key.drop_platform() not in platform_independent_deps
    }
    assert all(key.platform is not None for key in platform_specific_deps)

    combined = {**platform_independent_deps, **editable_deps, **platform_specific_deps}
    return combined
