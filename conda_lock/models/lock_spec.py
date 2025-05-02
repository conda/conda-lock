import hashlib
import json
import pathlib
import typing

from typing import Dict, List, Optional, Union, cast

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from conda_lock.content_hash_types import (
    EmptyDict,
    PlatformSubdirStr,
    SerializedDependency,
    SerializedLockspec,
    SubdirMetadata,
)
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
    subdirectory: Optional[str] = None


class PathDependency(_BaseDependency):
    path: str
    is_directory: bool
    subdirectory: Optional[str] = None


Dependency = Union[VersionedDependency, URLDependency, VCSDependency, PathDependency]


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
    ) -> Dict[PlatformSubdirStr, str]:
        result: dict[PlatformSubdirStr, str] = {}
        for platform in self.platforms:
            content = self.content_for_platform(platform, virtual_package_repo)
            env_spec = json.dumps(content, sort_keys=True)
            hash = hashlib.sha256(env_spec.encode("utf-8")).hexdigest()
            result[platform] = hash
        return result

    def content_hash_for_platform(
        self, platform: PlatformSubdirStr, virtual_package_repo: Optional[FakeRepoData]
    ) -> str:
        return self.content_hash(virtual_package_repo)[platform]

    def content_for_platform(
        self, platform: PlatformSubdirStr, virtual_package_repo: Optional[FakeRepoData]
    ) -> SerializedLockspec:
        data: SerializedLockspec = {
            "channels": [c.model_dump_json() for c in self.channels],
            "specs": [
                cast(SerializedDependency, p.model_dump())
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

            # We don't actually use these values! I'm including them to indicate
            # what I would have expected from the schema. See the code block
            # immediately below for the actual values.
            fallback_noarch: Union[SubdirMetadata, EmptyDict] = {
                "info": {"subdir": "noarch"},
                "packages": {},
            }
            fallback_platform: Union[SubdirMetadata, EmptyDict] = {
                "info": {"subdir": platform},
                "packages": {},
            }

            # It seems a bit of a schema violation, but the original implementation
            # did this, so we have to keep it in order to preserve consistency of
            # the hashes.
            fallback_noarch = {}
            fallback_platform = {}

            data["virtual_package_hash"] = {
                "noarch": vpr_data.get("noarch", fallback_noarch),
                platform: vpr_data.get(platform, fallback_platform),
            }
        return data

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
