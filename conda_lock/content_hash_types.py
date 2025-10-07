"""Type definitions related to computing content hashes.

There is incidentally also a lot of virtual package stuff here.
"""

from typing import (
    Literal,
    TypeAlias,
)

from typing_extensions import NotRequired, TypedDict


# Use TypeAlias to be descriptive about what kinds of strings are expected.
# This retains the flexibility of str, avoiding the need for awkward casting.
PlatformSubdirStr: TypeAlias = str
PackageNameStr: TypeAlias = str


class HashableVirtualPackage(TypedDict):
    """A dict that represents a fake package when computing the lockfile content hash"""

    name: PackageNameStr
    version: str
    build_string: str
    build_number: int
    build: str
    noarch: str
    depends: list[str]
    timestamp: int
    package_type: str | None
    subdir: PlatformSubdirStr


class SerializedDependency(TypedDict):
    # _BaseDependency fields:
    name: str
    manager: Literal["conda", "pip"]
    category: str
    extras: list[str]
    markers: NotRequired[str | None]
    # Note, markers was added in conda-lock v3

    # VersionedDependency fields:
    version: NotRequired[str]
    build: NotRequired[str | None]
    conda_channel: NotRequired[str | None]
    hash: NotRequired[str | None]

    # URLDependency fields:
    url: NotRequired[str]
    hashes: NotRequired[list[str]]

    # VCSDependency fields:
    source: NotRequired[str]
    vcs: NotRequired[str]
    rev: NotRequired[str | None]
    subdirectory: NotRequired[str | None]

    # PathDependency fields:
    path: NotRequired[str]
    is_directory: NotRequired[bool]
    # # Also in VCSDependency:
    # subdirectory: NotRequired[Optional[str]]


class RepoMetadataInfo(TypedDict):
    subdir: PlatformSubdirStr


class SubdirMetadata(TypedDict):
    info: RepoMetadataInfo
    packages: dict[PackageNameStr, HashableVirtualPackage]


class EmptyDict(TypedDict):
    pass


HashableVirtualPackageRepresentation: "TypeAlias" = dict[
    PlatformSubdirStr, SubdirMetadata | EmptyDict
]


class SerializedLockspec(TypedDict):
    channels: list[str]
    specs: list[SerializedDependency]
    pip_repositories: NotRequired[list[str]]
    virtual_package_hash: NotRequired[HashableVirtualPackageRepresentation]
