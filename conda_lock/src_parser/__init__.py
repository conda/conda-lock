import hashlib
import json
import logging
import pathlib
import typing

from itertools import chain
from typing import AbstractSet, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, validator
from typing_extensions import Literal

from conda_lock.common import ordered_union, suffix_union
from conda_lock.errors import ChannelAggregationError
from conda_lock.models import StrictModel
from conda_lock.models.channel import Channel
from conda_lock.virtual_package import FakeRepoData


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]

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
    selectors: Selectors = Selectors()


class VersionedDependency(_BaseDependency):
    version: str
    build: Optional[str] = None
    conda_channel: Optional[str] = None


class URLDependency(_BaseDependency):
    url: str
    hashes: List[str]


Dependency = Union[VersionedDependency, URLDependency]


class Package(StrictModel):
    url: str
    hash: str


class LockSpecification(BaseModel):
    dependencies: List[Dependency]
    # TODO: Should we store the auth info in here?
    channels: List[Channel]
    platforms: List[str]
    sources: List[pathlib.Path]
    virtual_package_repo: Optional[FakeRepoData] = None
    allow_pypi_requests: bool = True

    def content_hash(self) -> Dict[str, str]:
        return {
            platform: self.content_hash_for_platform(platform)
            for platform in self.platforms
        }

    def content_hash_for_platform(self, platform: str) -> str:
        data = {
            "channels": [c.json() for c in self.channels],
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

    @validator("channels", pre=True)
    def validate_channels(cls, v: List[Union[Channel, str]]) -> List[Channel]:
        for i, e in enumerate(v):
            if isinstance(e, str):
                v[i] = Channel.from_string(e)
        return typing.cast(List[Channel], v)


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


def parse_source_files(
    src_files: List[pathlib.Path],
    platform_overrides: Optional[Sequence[str]],
    pip_support: bool = True,
) -> List[LockSpecification]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    platform_overrides :
        Target platforms to render environment.yaml and meta.yaml files for
    """
    from conda_lock.src_parser.environment_yaml import parse_environment_file
    from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
    from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml

    desired_envs: List[LockSpecification] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(
                parse_meta_yaml_file(
                    src_file, list(platform_overrides or DEFAULT_PLATFORMS)
                )
            )
        elif src_file.name == "pyproject.toml":
            desired_envs.append(parse_pyproject_toml(src_file))
        else:
            desired_envs.append(
                parse_environment_file(
                    src_file,
                    platform_overrides,
                    default_platforms=DEFAULT_PLATFORMS,
                    pip_support=pip_support,
                )
            )
    return desired_envs


def make_lock_spec(
    *,
    src_files: List[pathlib.Path],
    virtual_package_repo: FakeRepoData,
    channel_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    required_categories: Optional[AbstractSet[str]] = None,
    pip_support: bool = True,
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files.  If required_categories is set filter out specs that do not match those"""
    lock_specs = parse_source_files(
        src_files=src_files,
        platform_overrides=platform_overrides,
        pip_support=pip_support,
    )

    lock_spec = aggregate_lock_specs(lock_specs)
    lock_spec.virtual_package_repo = virtual_package_repo
    lock_spec.channels = (
        [Channel.from_string(co) for co in channel_overrides]
        if channel_overrides
        else lock_spec.channels
    )
    lock_spec.platforms = (
        list(platform_overrides) if platform_overrides else lock_spec.platforms
    ) or list(DEFAULT_PLATFORMS)

    if required_categories is not None:

        def dep_has_category(d: Dependency, categories: AbstractSet[str]) -> bool:
            return d.category in categories

        lock_spec.dependencies = [
            d
            for d in lock_spec.dependencies
            if dep_has_category(d, categories=required_categories)
        ]

    return lock_spec
