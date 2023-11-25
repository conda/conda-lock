from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import typing

from fnmatch import fnmatchcase
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, validator
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

    @validator("extras")
    def sorted_extras(cls, v: List[str]) -> List[str]:
        return sorted(v)

    def _merge_base(self, other: _BaseDependency) -> _BaseDependency:
        if other is None:
            return self
        if (
            self.name != other.name
            or self.manager != other.manager
            or self.category != other.category
        ):
            raise ValueError(
                "Cannot merge incompatible dependencies: {self} != {other}"
            )
        return _BaseDependency(
            name=self.name,
            manager=self.manager,
            category=self.category,
            extras=list(set(self.extras + other.extras)),
        )


class VersionedDependency(_BaseDependency):
    version: str
    build: Optional[str] = None
    conda_channel: Optional[str] = None

    @staticmethod
    def _merge_versions(
        version1: str,
        version2: str,
    ) -> str:
        if version1 == version2:
            return version1
        if version1 == "" or version1 == "*":
            return version2
        if version2 == "" or version2 == "*":
            return version1
        return f"{version1},{version2}"

    @staticmethod
    def _merge_builds(
        build1: Optional[str],
        build2: Optional[str],
    ) -> Optional[str]:
        if build1 == build2:
            return build1
        if build1 is None or build1 == "":
            return build2
        if build2 is None or build2 == "":
            return build1
        if fnmatchcase(build1, build2):
            return build1
        if fnmatchcase(build2, build1):
            return build2
        raise ValueError(f"Found incompatible constraint {build1}, {build2}")

    def merge(self, other: Optional[VersionedDependency]) -> VersionedDependency:
        if other is None:
            return self

        if (
            self.conda_channel is not None
            and other.conda_channel is not None
            and self.conda_channel != other.conda_channel
        ):
            raise ValueError(
                f"VersionedDependency has two different conda_channels:\n{self}\n{other}"
            )
        merged_base = self._merge_base(other)
        try:
            build = self._merge_builds(self.build, other.build)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported usage of two incompatible builds for same dependency {self}, {other}"
            ) from exc

        return VersionedDependency(
            name=merged_base.name,
            manager=merged_base.manager,
            category=merged_base.category,
            extras=merged_base.extras,
            version=self._merge_versions(self.version, other.version),
            build=build,
            conda_channel=self.conda_channel or other.conda_channel,
        )


class URLDependency(_BaseDependency):
    url: str
    hashes: List[str]

    def merge(self, other: Optional[URLDependency]) -> URLDependency:
        if other is None:
            return self
        if self.url != other.url:
            raise ValueError(f"URLDependency has two different urls:\n{self}\n{other}")

        if self.hashes != other.hashes:
            raise ValueError(
                f"URLDependency has two different hashess:\n{self}\n{other}"
            )
        merged_base = self._merge_base(other)

        return URLDependency(
            name=merged_base.name,
            manager=merged_base.manager,
            category=merged_base.category,
            extras=merged_base.extras,
            url=self.url,
            hashes=self.hashes,
        )


class VCSDependency(_BaseDependency):
    source: str
    vcs: str
    rev: Optional[str] = None

    def merge(self, other: Optional[VCSDependency]) -> VCSDependency:
        if other is None:
            return self
        if self.source != other.source:
            raise ValueError(
                f"VCSDependency has two different sources:\n{self}\n{other}"
            )

        if self.vcs != other.vcs:
            raise ValueError(f"VCSDependency has two different vcss:\n{self}\n{other}")

        if self.rev is not None and other.rev is not None and self.rev != other.rev:
            raise ValueError(f"VCSDependency has two different revs:\n{self}\n{other}")
        merged_base = self._merge_base(other)

        return VCSDependency(
            name=merged_base.name,
            manager=merged_base.manager,
            category=merged_base.category,
            extras=merged_base.extras,
            source=self.source,
            vcs=self.vcs,
            rev=self.rev or other.rev,
        )


Dependency = Union[VersionedDependency, URLDependency, VCSDependency]


class Package(StrictModel):
    url: str
    hash: str


class PoetryMappedDependencySpec(StrictModel):
    url: Optional[str]
    manager: Literal["conda", "pip"]
    extras: List
    poetry_version_spec: Optional[str]


class LockSpecification(BaseModel):
    dependencies: Dict[str, List[Dependency]]
    # TODO: Should we store the auth info in here?
    channels: List[Channel]
    sources: List[pathlib.Path]
    pip_repositories: List[PipRepository] = Field(default_factory=list)
    virtual_package_repo: Optional[FakeRepoData] = None
    allow_pypi_requests: bool = True

    @property
    def platforms(self) -> List[str]:
        return list(self.dependencies.keys())

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
                for p in sorted(
                    self.dependencies[platform], key=lambda p: (p.manager, p.name)
                )
            ],
        }
        if self.pip_repositories:
            data["pip_repositories"] = [repo.json() for repo in self.pip_repositories]
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
                e = Channel.from_string(e)
                v[i] = e
            if e.url == "nodefaults":
                raise ValueError("nodefaults channel is not allowed, ref #418")
        return typing.cast(List[Channel], v)

    @validator("pip_repositories", pre=True)
    def validate_pip_repositories(
        cls, value: List[Union[PipRepository, str]]
    ) -> List[PipRepository]:
        for index, repository in enumerate(value):
            if isinstance(repository, str):
                value[index] = PipRepository.from_string(repository)
        return typing.cast(List[PipRepository], value)
