import pathlib
import typing

from typing import Optional, Union

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from conda_lock.models import StrictModel
from conda_lock.models.channel import Channel
from conda_lock.models.pip_repository import PipRepository


class _BaseDependency(StrictModel):
    name: str
    manager: Literal["conda", "pip"] = "conda"
    category: str = "main"
    extras: list[str] = []
    markers: Optional[str] = None

    @field_validator("extras")
    @classmethod
    def sorted_extras(cls, v: list[str]) -> list[str]:
        return sorted(v)


class VersionedDependency(_BaseDependency):
    version: str
    build: Optional[str] = None
    conda_channel: Optional[str] = None
    hash: Optional[str] = None


class URLDependency(_BaseDependency):
    url: str
    hashes: list[str]


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
    extras: list
    markers: Optional[str] = None
    poetry_version_spec: Optional[str] = None


class LockSpecification(BaseModel):
    dependencies: dict[str, list[Dependency]]
    # TODO: Should we store the auth info in here?
    channels: list[Channel]
    sources: list[pathlib.Path]
    pip_repositories: list[PipRepository] = Field(default_factory=list)
    allow_pypi_requests: bool = True

    @property
    def platforms(self) -> list[str]:
        return list(self.dependencies.keys())

    @field_validator("channels", mode="before")
    @classmethod
    def validate_channels(cls, v: list[Union[Channel, str]]) -> list[Channel]:
        for i, e in enumerate(v):
            if isinstance(e, str):
                e = Channel.from_string(e)
                v[i] = e
            if e.url == "nodefaults":
                raise ValueError("nodefaults channel is not allowed, ref #418")
        return typing.cast(list[Channel], v)

    @field_validator("pip_repositories", mode="before")
    @classmethod
    def validate_pip_repositories(
        cls, value: list[Union[PipRepository, str]]
    ) -> list[PipRepository]:
        for index, repository in enumerate(value):
            if isinstance(repository, str):
                value[index] = PipRepository.from_string(repository)
        return typing.cast(list[PipRepository], value)
