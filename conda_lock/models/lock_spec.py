import hashlib
import json
import pathlib
import typing
import warnings

from collections import defaultdict
from typing import Any, Dict, List, NamedTuple, Optional, Set, Union

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from conda_lock.models import StrictModel
from conda_lock.models.channel import Channel
from conda_lock.models.pip_repository import PipRepository
from conda_lock.virtual_package import FakeRepoData


class _BaseDependency(StrictModel):
    name: str
    manager: Literal["conda", "pip"] = "conda"
    category: str = "main"
    extras: List[str] = []
    markers: Optional[str] = None

    @field_validator("extras")
    @classmethod
    def sorted_extras(cls, v: List[str]) -> List[str]:
        return sorted(v)


class VersionedDependency(_BaseDependency):
    version: str
    build: Optional[str] = None
    conda_channel: Optional[str] = None
    hash: Optional[str] = None


class URLDependency(_BaseDependency):
    url: str
    hashes: List[str]


class VCSDependency(_BaseDependency):
    source: str
    vcs: str
    rev: Optional[str] = None


Dependency = Union[VersionedDependency, URLDependency, VCSDependency]


class Package(StrictModel):
    url: str
    hash: str


class PoetryMappedDependencySpec(StrictModel):
    url: Optional[str] = None
    manager: Literal["conda", "pip"]
    extras: List
    markers: Optional[str] = None
    poetry_version_spec: Optional[str] = None


class LockSpecification(BaseModel):
    dependencies: Dict[str, List[Dependency]]
    # TODO: Should we store the auth info in here?
    channels: List[Channel]
    sources: List[pathlib.Path]
    pip_repositories: List[PipRepository] = Field(default_factory=list)
    allow_pypi_requests: bool = True

    @property
    def platforms(self) -> List[str]:
        return list(self.dependencies.keys())

    def content_hash(
        self, virtual_package_repo: Optional[FakeRepoData]
    ) -> Dict[str, str]:
        return {
            platform: self.content_hash_for_platform(platform, virtual_package_repo)
            for platform in self.platforms
        }

    def content_hash_for_platform(
        self, platform: str, virtual_package_repo: Optional[FakeRepoData]
    ) -> str:
        data = {
            "channels": [c.model_dump_json() for c in self.channels],
            "specs": [
                p.model_dump()
                for p in sorted(
                    self.dependencies[platform], key=lambda p: (p.manager, p.name)
                )
            ],
        }
        if self.pip_repositories:
            data["pip_repositories"] = [
                repo.model_dump_json() for repo in self.pip_repositories
            ]
        if virtual_package_repo is not None:
            vpr_data = virtual_package_repo.all_repodata
            data["virtual_package_hash"] = {
                "noarch": vpr_data.get("noarch", {}),
                **{platform: vpr_data.get(platform, {})},
            }

        env_spec = json.dumps(data, sort_keys=True)
        return hashlib.sha256(env_spec.encode("utf-8")).hexdigest()

    @field_validator("channels", mode="before")
    @classmethod
    def validate_channels(cls, v: List[Union[Channel, str]]) -> List[Channel]:
        for i, e in enumerate(v):
            if isinstance(e, str):
                e = Channel.from_string(e)
                v[i] = e
            if e.url == "nodefaults":
                raise ValueError("nodefaults channel is not allowed, ref #418")
        return typing.cast(List[Channel], v)

    @field_validator("pip_repositories", mode="before")
    @classmethod
    def validate_pip_repositories(
        cls, value: List[Union[PipRepository, str]]
    ) -> List[PipRepository]:
        for index, repository in enumerate(value):
            if isinstance(repository, str):
                value[index] = PipRepository.from_string(repository)
        return typing.cast(List[PipRepository], value)


class DepKey1(NamedTuple):
    name: str
    category: str
    platform: str
    manager: str


class DepKey2(NamedTuple):
    name: str
    category: str
    manager: str


class DepWithPlatform(NamedTuple):
    dep: Dependency
    platform: str


class DepWithSubdir(NamedTuple):
    dep: Dependency
    subdir: Optional[str]


def render_pixi_toml(  # noqa: C901
    *,
    lock_spec: LockSpecification,
    project_name: str = "project-name-placeholder",
) -> List[str]:
    all_platforms = lock_spec.dependencies.keys()

    all_categories: Set[str] = set()
    for platform in all_platforms:
        for dep in lock_spec.dependencies[platform]:
            all_categories.add(dep.category)
    if {"main", "default"} <= all_categories:
        raise ValueError("Cannot have both 'main' and 'default' as categories/extras")

    indexed_deps: Dict[DepKey1, Dependency] = {}
    for platform in all_platforms:
        deps = lock_spec.dependencies[platform]
        for dep in deps:
            category = dep.category
            key1 = DepKey1(
                name=dep.name, category=category, platform=platform, manager=dep.manager
            )
            if key1 in indexed_deps:
                raise ValueError(
                    f"Duplicate dependency {key1}: {dep}, {indexed_deps[key1]}"
                )
            indexed_deps[key1] = dep

    # Collect by platform
    aggregated_deps: Dict[DepKey2, List[DepWithPlatform]] = defaultdict(list)
    for key1, dep in indexed_deps.items():
        key2 = DepKey2(name=key1.name, category=key1.category, manager=key1.manager)
        aggregated_deps[key2].append(DepWithPlatform(dep=dep, platform=key1.platform))

    # Reduce by platform
    reduced_deps: Dict[DepKey2, DepWithSubdir] = {}
    for key2, deps_with_platforms in aggregated_deps.items():
        for curr, next in zip(deps_with_platforms, deps_with_platforms[1:]):
            if curr.dep != next.dep:
                raise ValueError(f"Conflicting dependencies {curr} and {next}")
        dep = deps_with_platforms[0].dep
        dep_platforms = {dep.platform for dep in deps_with_platforms}
        if len(dep_platforms) < len(all_platforms):
            if len(dep_platforms) != 1:
                raise ValueError(
                    f"Dependency {dep} is specified for more than one platform but not all: {dep_platforms=}, {all_platforms=}"
                )
            reduced_deps[key2] = DepWithSubdir(dep=dep, subdir=dep_platforms.pop())
        elif len(dep_platforms) == len(all_platforms):
            reduced_deps[key2] = DepWithSubdir(dep=dep, subdir=None)
        else:
            raise RuntimeError(f"Impossible: {dep_platforms=}, {all_platforms=}")

    result: List[str] = [
        "# This file was generated by conda-lock for the pixi environment manager.",
        "# For more information, see <https://pixi.sh>." "# Source files:",
    ]
    for source in lock_spec.sources:
        result.append(f"# - {source}")
    result.extend(
        [
            "",
            "[project]",
            f'name = "{project_name}"',
            f"platforms = {list(all_platforms)}",
        ]
    )
    channels: List[str] = [channel.url for channel in lock_spec.channels]
    for channel in lock_spec.channels:
        if channel.used_env_vars:
            warnings.warn(
                f"Channel {channel.url} uses environment variables "
                "which are not implemented"
            )
    result.append(f"channels = {channels}")
    result.append("")

    sorted_categories = sorted(all_categories)
    if "main" in sorted_categories:
        sorted_categories.remove("main")
        sorted_categories.insert(0, "main")
    if "default" in sorted_categories:
        sorted_categories.remove("default")
        sorted_categories.insert(0, "default")

    for category in sorted_categories:
        feature = category if category != "main" else "default"

        conda_deps: Dict[str, DepWithSubdir] = {}
        pip_deps: Dict[str, Dependency] = {}
        for key2, dep_with_subdir in sorted(
            reduced_deps.items(), key=lambda x: x[0].name
        ):
            if key2.category == category:
                name = key2.name
                manager = dep_with_subdir.dep.manager
                if manager == "conda":
                    conda_deps[name] = dep_with_subdir
                elif manager == "pip":
                    if dep_with_subdir.subdir is not None:
                        raise ValueError(
                            f"Subdir specified for pip dependency {dep_with_subdir}"
                        )
                    else:
                        pip_deps[name] = dep_with_subdir.dep
                else:
                    raise ValueError(f"Unknown manager {manager}")

        if conda_deps:
            if feature == "default":
                result.append("[dependencies]")
            else:
                result.append(f"[feature.{feature}.dependencies]")
            for name, dep_with_subdir in conda_deps.items():
                pixi_spec = make_pixi_conda_spec(dep_with_subdir)
                result.append(f"{name} = {pixi_spec}")
            result.append("")

        if pip_deps:
            if feature == "default":
                result.append("[pypi-dependencies]")
            else:
                result.append(f"[feature.{feature}.pypi-dependencies]")
            for name, dep in pip_deps.items():
                pixi_spec = make_pixi_pip_spec(dep)
                result.append(f"{name} = {pixi_spec}")
            result.append("")
    return result


def make_pixi_conda_spec(dep_with_subdir: DepWithSubdir) -> str:
    dep = dep_with_subdir.dep
    subdir = dep_with_subdir.subdir
    matchspec = {}
    if dep.extras:
        warnings.warn(f"Extras not supported in Conda dep {dep}")
    if isinstance(dep, VersionedDependency):
        matchspec["version"] = dep.version or "*"
        if subdir is not None:
            matchspec["subdir"] = subdir
        if dep.build is not None:
            matchspec["build"] = dep.build
        if dep.hash is not None:
            raise NotImplementedError(f"Hash not yet supported in {dep}")
        if dep.conda_channel is not None:
            matchspec["channel"] = dep.conda_channel
        if len(matchspec) == 1:
            return f'"{matchspec["version"]}"'
        else:
            return json.dumps(matchspec)
    elif isinstance(dep, URLDependency):
        raise NotImplementedError(f"URL not yet supported in {dep}")
    elif isinstance(dep, VCSDependency):
        raise NotImplementedError(f"VCS not yet supported in {dep}")
    else:
        raise ValueError(f"Unknown dependency type {dep}")


def make_pixi_pip_spec(dep: Dependency) -> str:
    matchspec: Dict[str, Any] = {}
    if isinstance(dep, VersionedDependency):
        matchspec["version"] = dep.version or "*"
        if dep.hash is not None:
            raise NotImplementedError(f"Hash not yet supported in {dep}")
        if dep.conda_channel is not None:
            matchspec["channel"] = dep.conda_channel
        if dep.extras:
            matchspec["extras"] = dep.extras
        if len(matchspec) == 1:
            return f'"{matchspec["version"]}"'
        else:
            return json.dumps(matchspec)
    elif isinstance(dep, URLDependency):
        raise NotImplementedError(f"URL not yet supported in {dep}")
    elif isinstance(dep, VCSDependency):
        raise NotImplementedError(f"VCS not yet supported in {dep}")
    else:
        raise ValueError(f"Unknown dependency type {dep}")
