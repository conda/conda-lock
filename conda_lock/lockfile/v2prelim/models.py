from collections import defaultdict
from typing import ClassVar, Dict, List, Optional

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
    category: str = "main"

    def to_v1(self) -> List[LockedDependencyV1]:
        """Convert a v2 dependency into a list of v1 dependencies.

        In case a v2 dependency might contain multiple categories, but a v1 dependency
        can only contain a single category, we represent multiple categories as a list
        of v1 dependencies that are identical except for the `category` field. The
        `category` field runs over all categories."""
        package_entries_per_category = [
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
            for category in sorted({self.category})
        ]
        return package_entries_per_category


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
        return Lockfile(package=final_package, metadata=self.metadata | other.metadata)

    def toposort_inplace(self) -> None:
        self.package = self._toposort(self.package)

    def alphasort_inplace(self) -> None:
        # Sort the packages themselves by key (conda/pip, name, platform)
        self.package.sort(key=lambda d: d.key())
        for p in self.package:
            # Also ensure that the dependencies of each package are sorted
            # <https://github.com/conda/conda-lock/pull/654#issuecomment-2198453427>
            p.dependencies = {
                name: spec for name, spec in sorted(p.dependencies.items())
            }

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
        # Each v2 package gives a list of v1 packages.
        # Flatten these into a single list of v1 packages.
        v1_packages = [
            package_entry_per_category
            for p in self.package
            for package_entry_per_category in p.to_v1()
        ]
        return LockfileV1(
            package=v1_packages,
            metadata=self.metadata,
        )


def _locked_dependency_v1_to_v2(dep: LockedDependencyV1) -> LockedDependency:
    """Convert a LockedDependency from v1 to v2.

    * Remove the optional field (it is always equal to category != "main")
    """
    return LockedDependency(
        name=dep.name,
        version=dep.version,
        manager=dep.manager,
        platform=dep.platform,
        dependencies=dep.dependencies,
        url=dep.url,
        hash=dep.hash,
        category=dep.category,
        source=dep.source,
        build=dep.build,
    )


def lockfile_v1_to_v2(lockfile_v1: LockfileV1) -> Lockfile:
    """Convert a Lockfile from v1 to v2."""
    return Lockfile(
        package=[_locked_dependency_v1_to_v2(p) for p in lockfile_v1.package],
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
