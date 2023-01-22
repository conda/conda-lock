import hashlib
import json
import logging
import pathlib
import typing

from itertools import chain
from typing import (
    AbstractSet,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from pydantic import BaseModel, validator
from typing_extensions import Literal

from conda_lock.common import suffix_union
from conda_lock.errors import ChannelAggregationError, DependencyAggregationError
from conda_lock.models import StrictModel
from conda_lock.models.channel import Channel
from conda_lock.virtual_package import FakeRepoData


DEFAULT_PLATFORMS = {"osx-64", "linux-64", "win-64"}

logger = logging.getLogger(__name__)


class Selectors(StrictModel):
    platform: Optional[List[str]] = None

    def __ior__(self, other: "Selectors") -> "Selectors":
        if not isinstance(other, Selectors):
            raise TypeError
        if other.platform and self.platform:
            for p in other.platform:
                if p not in self.platform:
                    self.platform.append(p)
        return self

    def for_platform(self, platform: str) -> bool:
        return self.platform is None or platform in self.platform


class _BaseDependency(StrictModel):
    name: str
    manager: Literal["conda", "pip"] = "conda"
    optional: bool = False
    category: str = "main"
    extras: List[str] = []

    def to_source(self) -> "SourceDependency":
        return SourceDependency(dep=self)  # type: ignore


class VersionedDependency(_BaseDependency):
    version: str
    build: Optional[str] = None
    conda_channel: Optional[str] = None


class URLDependency(_BaseDependency):
    url: str
    hashes: List[str]


Dependency = Union[VersionedDependency, URLDependency]


class SourceDependency(StrictModel):
    dep: Dependency
    selectors: Selectors = Selectors()


class Package(StrictModel):
    url: str
    hash: str


class SourceFile(StrictModel):
    file: pathlib.Path
    dependencies: List[SourceDependency]
    # TODO: Should we store the auth info in here?
    channels: List[Channel]
    platforms: Set[str]

    @validator("channels", pre=True)
    def validate_channels(cls, v: List[Union[Channel, str]]) -> List[Channel]:
        for i, e in enumerate(v):
            if isinstance(e, str):
                v[i] = Channel.from_string(e)
        return typing.cast(List[Channel], v)

    def spec(self, platform: str) -> List[Dependency]:
        from conda_lock.src_parser.selectors import dep_in_platform_selectors

        return [
            dep.dep
            for dep in self.dependencies
            if dep.selectors.platform is None
            or dep_in_platform_selectors(dep, platform)
        ]


class LockSpecification(BaseModel):
    dependencies: Dict[str, List[Dependency]]
    # TODO: Should we store the auth info in here?
    channels: List[Channel]
    sources: List[pathlib.Path]
    virtual_package_repo: Optional[FakeRepoData] = None

    @property
    def platforms(self) -> List[str]:
        return list(self.dependencies.keys())

    def content_hash(self) -> Dict[str, str]:
        return {
            platform: self.content_hash_for_platform(platform)
            for platform in self.dependencies.keys()
        }

    def content_hash_for_platform(self, platform: str) -> str:
        data = {
            "channels": [c.json() for c in self.channels],
            "specs": [
                p.dict()
                for p in sorted(
                    self.dependencies[platform], key=lambda p: (p.manager, p.name)
                )
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

    @validator("channels", pre=True)
    def validate_channels(cls, v: List[Union[Channel, str]]) -> List[Channel]:
        for i, e in enumerate(v):
            if isinstance(e, str):
                v[i] = Channel.from_string(e)
        return typing.cast(List[Channel], v)


def merge_dependencies(
    dep_a: Dependency,
    dep_b: Dependency,
) -> Dependency:
    """
    Merge 2 Dependency Specifications Together if Valid
    Either by comparing URL locations or by combining the Versioning Code
    """
    assert dep_a.manager == dep_b.manager and dep_a.name == dep_b.name

    if isinstance(dep_a, URLDependency) and isinstance(dep_b, URLDependency):
        if dep_a != dep_b:
            raise DependencyAggregationError(
                f"Found conflicting URL dependency specifications for {dep_a.name} on {dep_a.manager}:\n"
                f"  URL 1: {dep_a.url}\n  URL 2: {dep_b.url}"
            )
        return dep_a

    # If bold old and new are VersionedDependency, combine version strings together
    # If there are conflicting versions, they will be handled by the solver
    if isinstance(dep_a, VersionedDependency) and isinstance(
        dep_b, VersionedDependency
    ):
        if dep_a.manager == "pip":
            if not dep_a.version:
                vstr = dep_b.version
            elif not dep_b.version:
                vstr = dep_a.version
            else:
                vstr = f"{dep_a.version},{dep_b.version}"

            return VersionedDependency(
                name=dep_a.name,
                version=vstr,
                manager="pip",
                optional=dep_a.optional,
                category=dep_a.category,
                extras=dep_a.extras,
            )

        from conda_lock.src_parser.conda_common import merge_version_specs

        return VersionedDependency(
            name=dep_a.name,
            version=merge_version_specs(dep_a.version, dep_b.version),
            manager="conda",
            optional=dep_a.optional,
            category=dep_a.category,
            extras=dep_a.extras,
        )

    # Case when one dependency specifies a version and another a URL
    raise DependencyAggregationError(
        f"Found both a URL and Version Dependency Specification for {dep_a.name} on {dep_a.manager}."
        "They can not be combined or solved together."
    )


def aggregate_deps(grouped_deps: List[List[Dependency]]) -> List[Dependency]:
    # List unique dependencies
    unique_deps: Dict[Tuple[str, str], Dependency] = {}
    for dep in chain.from_iterable(grouped_deps):
        key = (dep.manager, dep.name)
        if key in unique_deps:
            unique_deps[key] = merge_dependencies(unique_deps[key], dep)
        else:
            unique_deps[key] = dep

    return list(unique_deps.values())


def aggregate_channels(
    channels: Iterable[List[Channel]],
    channel_overrides: Optional[Sequence[str]] = None,
) -> List[Channel]:
    if channel_overrides:
        return [Channel.from_string(co) for co in channel_overrides]
    else:
        # Ensure channels are correctly ordered
        try:
            return suffix_union(channels)
        except ValueError as e:
            raise ChannelAggregationError(*e.args)


def parse_source_files(
    src_file_paths: List[pathlib.Path], pip_support: bool = True
) -> List[SourceFile]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    pip_support :
        Support pip dependencies
    """
    from conda_lock.src_parser.environment_yaml import parse_environment_file
    from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
    from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml

    src_files: List[SourceFile] = []
    for src_file_path in src_file_paths:
        if src_file_path.name in ("meta.yaml", "meta.yml"):
            src_files.append(parse_meta_yaml_file(src_file_path))
        elif src_file_path.name == "pyproject.toml":
            src_files.append(parse_pyproject_toml(src_file_path))
        else:
            src_files.append(
                parse_environment_file(
                    src_file_path,
                    pip_support=pip_support,
                )
            )
    return src_files


def make_lock_spec(
    *,
    src_file_paths: List[pathlib.Path],
    virtual_package_repo: FakeRepoData,
    channel_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Set[str]] = None,
    required_categories: Optional[AbstractSet[str]] = None,
    pip_support: bool = True,
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files. If required_categories is set filter out specs that do not match those"""
    src_files = parse_source_files(src_file_paths, pip_support)

    # Determine Platforms to Render for
    platforms = (
        platform_overrides
        or {plat for sf in src_files for plat in sf.platforms}
        or DEFAULT_PLATFORMS
    )

    spec = {
        plat: aggregate_deps([sf.spec(plat) for sf in src_files]) for plat in platforms
    }

    if required_categories is not None:
        spec = {
            plat: [d for d in deps if d.category in required_categories]
            for plat, deps in spec.items()
        }

    return LockSpecification(
        dependencies=spec,
        channels=aggregate_channels(
            (sf.channels for sf in src_files), channel_overrides
        ),
        sources=src_file_paths,
        virtual_package_repo=virtual_package_repo,
    )
