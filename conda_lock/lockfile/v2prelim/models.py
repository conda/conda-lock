from collections import defaultdict
from typing import ClassVar, Dict, List, Optional, Set

from conda_lock.lockfile.v1.models import (
    BaseLockedDependency,
    DependencySource,
    GitMeta,
    HashModel,
    InputMeta,
    LockMeta,
    MetadataOption,
    TimeMeta,
)
from conda_lock.lockfile.v1.models import LockedDependency as LockedDependencyV1
from conda_lock.lockfile.v1.models import Lockfile as LockfileV1
from conda_lock.models import StrictModel


class LockedDependency(BaseLockedDependency):
    categories: Set[str] = set()

    def to_v1(self) -> List[LockedDependencyV1]:
        return [
            LockedDependencyV1(
                name=self.name,
                version=self.version,
                manager=self.manager,
                platform=self.platform,
                dependencies=self.dependencies,
                url=self.url,
                hash=self.hash,
                category=category,
                source=self.source,
                build=self.build,
                optional=category != "main",
            )
            for category in sorted(self.categories)
        ]


class Lockfile(StrictModel):
    version: ClassVar[int] = 2

    package: List[LockedDependency]
    metadata: LockMeta

    def merge(self, other: "Optional[Lockfile]") -> "Lockfile":
        """
        merge self into other
        """
        if other is None:
            return self
        elif not isinstance(other, Lockfile):
            raise TypeError

        assert (
            self.metadata.channels == other.metadata.channels
        ), f"channels must match: {self.metadata.channels} != {other.metadata.channels}"

        ours = {d.key(): d for d in self.package}
        theirs = {d.key(): d for d in other.package}

        # Pick ours preferentially
        package: List[LockedDependency] = []
        for key in sorted(set(ours.keys()).union(theirs.keys())):
            if key not in ours or key[-1] not in self.metadata.platforms:
                package.append(theirs[key])
            else:
                package.append(ours[key])

        # Resort the conda packages topologically
        final_package = self._toposort(package)
        return Lockfile(package=final_package, metadata=other.metadata | self.metadata)

    def toposort_inplace(self) -> None:
        self.package = self._toposort(self.package)

    def alphasort_inplace(self) -> None:
        self.package.sort(key=lambda d: d.key())

    def filter_virtual_packages_inplace(self) -> None:
        self.package = [
            p
            for p in self.package
            if not (p.manager == "conda" and p.name.startswith("__"))
        ]

    @staticmethod
    def _toposort(package: List[LockedDependency]) -> List[LockedDependency]:
        platforms = {d.platform for d in package}

        # Resort the conda packages topologically
        final_package: List[LockedDependency] = []
        for platform in sorted(platforms):
            from conda_lock.interfaces.vendored_conda import toposort

            # Add the remaining non-conda packages in the order in which they appeared.
            # Order the pip packages topologically ordered (might be not 100% perfect if they depend on
            # other conda packages, but good enough
            for manager in ["conda", "pip"]:
                lookup = defaultdict(set)
                packages: Dict[str, LockedDependency] = {}

                for d in package:
                    if d.platform != platform:
                        continue

                    if d.manager != manager:
                        continue

                    lookup[d.name] = set(d.dependencies)
                    packages[d.name] = d

                ordered = toposort(lookup)
                for package_name in ordered:
                    # since we could have a pure dep in here, that does not have a package
                    # eg a pip package that depends on a conda package (the conda package will not be in this list)
                    dep = packages.get(package_name)
                    if dep is None:
                        continue
                    if dep.manager != manager:
                        continue
                    final_package.append(dep)

        return final_package

    def to_v1(self) -> LockfileV1:
        return LockfileV1(
            package=[out for p in self.package for out in p.to_v1()],
            metadata=self.metadata,
        )


def _locked_dependency_v1_to_v2(dep: List[LockedDependencyV1]) -> LockedDependency:
    """Convert a LockedDependency from v1 to v2.

    * Remove the optional field (it is always equal to category != "main")
    """
    assert len(dep) > 0
    assert all(d.key() == dep[0].key() for d in dep)
    assert len(set(d.category for d in dep)) == len(dep)

    return LockedDependency(
        name=dep[0].name,
        version=dep[0].version,
        manager=dep[0].manager,
        platform=dep[0].platform,
        dependencies=dep[0].dependencies,
        url=dep[0].url,
        hash=dep[0].hash,
        categories={d.category for d in dep},
        source=dep[0].source,
        build=dep[0].build,
    )


def lockfile_v1_to_v2(lockfile_v1: LockfileV1) -> Lockfile:
    """Convert a Lockfile from v1 to v2."""
    final_dependencies = defaultdict(list)
    for dep in lockfile_v1.package:
        final_dependencies[dep.key()].append(dep)

    return Lockfile(
        package=[
            _locked_dependency_v1_to_v2(v1_pkgs)
            for v1_pkgs in final_dependencies.values()
        ],
        metadata=lockfile_v1.metadata,
    )


class UpdateSpecification:
    def __init__(
        self,
        locked: Optional[List[LockedDependency]] = None,
        update: Optional[List[str]] = None,
    ):
        self.locked = locked or []
        self.update = update or []


__all__ = [
    "DependencySource",
    "GitMeta",
    "HashModel",
    "InputMeta",
    "LockedDependency",
    "Lockfile",
    "LockMeta",
    "MetadataOption",
    "TimeMeta",
    "UpdateSpecification",
    "lockfile_v1_to_v2",
]
