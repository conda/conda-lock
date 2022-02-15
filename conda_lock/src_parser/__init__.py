import hashlib
import json
import pathlib

from collections import defaultdict, namedtuple
from dataclasses import dataclass
from itertools import chain
from typing import ClassVar, Dict, List, Literal, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field, validator

from conda_lock.common import ordered_union
from conda_lock.lookup import normalize_conda_name
from conda_lock.virtual_package import FakeRepoData


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class Selectors(StrictModel):
    platform: Optional[List[str]] = None

    def __ior__(self, other) -> "Selectors":
        if not isinstance(other, Selectors):
            raise TypeError
        if other.platform and self.platform:
            for p in other.platform:
                if p not in self.platform:
                    self.platform.append(p)
        return self

    def for_platform(self, platform: str) -> bool:
        return self.platform is None or platform in self.platform


class Dependency(StrictModel):
    name: str
    manager: Literal["conda", "pip"] = "conda"
    optional: bool = False
    category: str = "main"
    extras: List[str] = []
    selectors: Selectors = Selectors()


class VersionedDependency(Dependency):
    version: str


class URLDependency(Dependency):
    url: str
    hashes: List[str]


class Package(StrictModel):
    url: str
    hash: str


class DependencySource(StrictModel):
    type: Literal["url"]
    url: str


LockKey = namedtuple("LockKey", ["manager", "name", "platform"])


class HashModel(StrictModel):
    md5: Optional[str] = None
    sha256: Optional[str] = None


class LockedDependency(StrictModel):
    name: str
    version: str
    manager: Literal["conda", "pip"]
    platform: str
    dependencies: Dict[str, str] = {}
    url: str
    hash: HashModel
    optional: bool = False
    category: str = "main"
    source: Optional[DependencySource] = None

    def key(self) -> LockKey:
        return LockKey(self.manager, self.name, self.platform)

    @validator("hash")
    def validate_hash(cls, v, values, **kwargs):
        if (values["manager"] == "conda") and (v.md5 is None):
            raise ValueError("conda package hashes must use MD5")
        return v


class LockMeta(StrictModel):
    content_hash: Dict[str, str] = Field(
        ..., description="Hash of dependencies for each target platform"
    )
    channels: List[str] = Field(
        ..., description="Channels used to resolve dependencies"
    )
    platforms: List[str] = Field(..., description="Target platforms")
    sources: List[str] = Field(
        ...,
        description="paths to source files, relative to the parent directory of the lockfile",
    )

    def __or__(self, other) -> "LockMeta":
        """merge other into self"""
        if other is None:
            return self
        elif not isinstance(other, LockMeta):
            raise TypeError

        return LockMeta(
            content_hash={**self.content_hash, **other.content_hash},
            channels=self.channels,
            platforms=sorted(set(self.platforms).union(other.platforms)),
            sources=ordered_union([self.sources, other.sources]),
        )


class Lockfile(StrictModel):

    version: ClassVar[int] = 1

    package: List[LockedDependency]
    metadata: LockMeta

    def __or__(self, other) -> "Lockfile":
        return other.__ror__(self)

    def __ror__(self, other) -> "Lockfile":
        """
        merge self into other
        """
        if other is None:
            return self
        elif not isinstance(other, Lockfile):
            raise TypeError

        assert self.metadata.channels == other.metadata.channels

        ours = {d.key(): d for d in self.package}
        theirs = {d.key(): d for d in other.package}

        # Pick ours preferentially
        package: List[LockedDependency] = []
        for key in sorted(set(ours.keys()).union(theirs.keys())):
            if key not in ours or key[-1] not in self.metadata.platforms:
                package.append(theirs[key])
            else:
                package.append(ours[key])

        platforms = {d.platform for d in package}

        # Resort the conda packages topologically
        final_package: List[LockedDependency] = []
        for platform in sorted(platforms):
            lookup = defaultdict(set)
            conda_packages: Dict[str, LockedDependency] = {}
            from ..vendor.conda.toposort import toposort

            for d in package:
                if d.platform != platform:
                    continue
                if d.manager != "conda":
                    continue
                lookup[d.name] = set(d.dependencies)
                conda_packages[d.name] = d

            ordered = toposort(lookup)
            for conda_package_name in ordered:
                d = conda_packages[conda_package_name]
                final_package.append(d)

            # Add the remaining non-conda packages in the order in which they appeared.
            # TOFO: should the pip packages also get topologically ordered?
            for d in package:
                if d.platform != platform:
                    continue
                if d.manager == "conda":
                    continue
                final_package.append(d)

        return Lockfile(package=final_package, metadata=other.metadata | self.metadata)


@dataclass
class LockSpecification:
    dependencies: List[Dependency]
    channels: List[str]
    platforms: List[str]
    sources: List[pathlib.Path]
    virtual_package_repo: Optional[FakeRepoData] = None

    def content_hash(self) -> Dict[str, str]:
        return {
            platform: self.content_hash_for_platform(platform)
            for platform in self.platforms
        }

    def content_hash_for_platform(self, platform: str) -> str:
        data: dict = {
            "channels": self.channels,
            "specs": [
                p.dict()
                for p in sorted(self.dependencies, key=lambda p: (p.manager, p.name))
                if p.selectors.for_platform(platform)
            ],
        }
        if self.virtual_package_repo is not None:
            vpr_data = self.virtual_package_repo.all_repodata
            data["virtual_package_hash"] = {
                "noarch": vpr_data.get("noarch", {}),
                **{platform: vpr_data.get(platform, {})},
            }

        env_spec = json.dumps(data, sort_keys=True)
        return hashlib.sha256(env_spec.encode("utf-8")).hexdigest()


def _apply_categories(
    requested: Dict[str, Dependency],
    planned: Dict[str, LockedDependency],
    categories: Sequence[str] = ("main", "dev"),
) -> None:
    """map each package onto the root request the with the highest-priority category"""
    # walk dependency tree to assemble all transitive dependencies by request
    dependents: Dict[str, Set[str]] = {}
    by_category = defaultdict(list)

    def seperator_munge_get(
        d: Dict[str, LockedDependency], key: str
    ) -> LockedDependency:
        # since separators are not consistent across managers we need to do some double attempts here
        try:
            return d[key]
        except KeyError:
            try:
                return d[key.replace("-", "_")]
            except KeyError:
                return d[key.replace("_", "-")]

    for name, request in requested.items():
        todo: List[str] = list()
        deps: Set[str] = set()
        item = name
        while True:
            todo.extend(
                dep
                for dep in seperator_munge_get(planned, item).dependencies
                # exclude virtual packages
                if not (dep in deps or dep.startswith("__"))
            )
            if todo:
                item = todo.pop(0)
                deps.add(item)
            else:
                break

        dependents[name] = deps

        by_category[request.category].append(request.name)

    # now, map each package to its root request
    categories = [*categories, *(k for k in by_category if k not in categories)]
    root_requests = {}
    for category in categories:
        for root in by_category.get(category, []):
            for transitive_dep in dependents[root]:
                if transitive_dep not in root_requests:
                    root_requests[transitive_dep] = root
    # include root requests themselves
    for name in requested:
        root_requests[name] = name

    for dep, root in root_requests.items():
        source = requested[root]
        target = seperator_munge_get(planned, normalize_conda_name(dep).lower())
        target.category = source.category
        target.optional = source.optional


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
            unique_deps[key].selectors |= dep.selectors
        else:
            unique_deps[key] = dep

    dependencies = list(unique_deps.values())

    return LockSpecification(
        dependencies,
        # uniquify metadata, preserving order
        channels=ordered_union(lock_spec.channels or [] for lock_spec in lock_specs),
        platforms=ordered_union(lock_spec.platforms or [] for lock_spec in lock_specs),
        sources=ordered_union(lock_spec.sources or [] for lock_spec in lock_specs),
    )


class UpdateSpecification:
    def __init__(
        self,
        locked: Optional[List[LockedDependency]] = None,
        update: Optional[List[str]] = None,
    ):
        self.locked = locked or []
        self.update = update or []
