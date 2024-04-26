import hashlib
import json
import pathlib
import typing

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
