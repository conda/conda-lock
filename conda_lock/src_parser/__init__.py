import hashlib
import json

from collections import defaultdict
from dataclasses import dataclass
from itertools import chain
from typing import Dict, List, Literal, Optional, Sequence, Set, Tuple, TypedDict

from pydantic import BaseModel, Field

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


class LockedDependency(StrictModel):
    name: str
    version: str
    manager: Literal["conda", "pip"]
    platforms: List[str]
    dependencies: Dict[str, str] = {}
    packages: Dict[str, Package]
    optional: bool = False
    category: str = "main"
    source: Optional[DependencySource] = None


class LockMeta(StrictModel):
    content_hash: str
    channels: List[str]
    platforms: List[str]


class Lockfile(StrictModel):
    package: List[LockedDependency]
    metadata: LockMeta


@dataclass
class LockSpecification:
    dependencies: List[Dependency]
    channels: List[str]
    platforms: List[str]
    virtual_package_repo: Optional[FakeRepoData] = None

    def content_hash(self) -> str:
        data: dict = {
            "channels": self.channels,
            "platforms": sorted(self.platforms),
            "specs": [
                p.dict()
                for p in sorted(self.dependencies, key=lambda p: (p.manager, p.name))
            ],
        }
        if self.virtual_package_repo is not None:
            vpr_data = self.virtual_package_repo.all_repodata
            data["virtual_package_hash"] = {
                "noarch": vpr_data.get("noarch", {}),
                **{platform: vpr_data.get(platform, {}) for platform in self.platforms},
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
    for name, request in requested.items():
        todo: List[str] = list()
        deps: Set[str] = set()
        item = name
        while True:
            todo.extend(
                dep
                for dep in planned[item].dependencies
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
        target = planned[dep]
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

    # unique channels, preserving order
    channels = list(
        {
            k: k
            for k in chain.from_iterable(
                [lock_spec.channels or [] for lock_spec in lock_specs]
            )
        }.values()
    )

    # unique platforms, preserving order
    platforms = list(
        {
            k: k
            for k in chain.from_iterable(
                [lock_spec.platforms or [] for lock_spec in lock_specs]
            )
        }.values()
    )

    return LockSpecification(dependencies, channels=channels, platforms=platforms)


class UpdateSpecification:
    def __init__(
        self,
        locked: Optional[List[LockedDependency]] = None,
        update: Optional[List[str]] = None,
    ):
        self.locked = locked or []
        self.update = update or []
