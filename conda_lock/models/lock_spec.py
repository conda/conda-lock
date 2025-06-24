import pathlib
import typing

from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from conda_lock.models import StrictModel
from conda_lock.models.channel import Channel
from conda_lock.models.pip_repository import PipRepository


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
