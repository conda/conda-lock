"""Type definitions related to computing content hashes.

There is incidentally also a lot of virtual package stuff here.
"""

from typing import (
    Dict,
    List,
    Literal,
    Optional,
    Union,
)

from typing_extensions import NotRequired, TypeAlias, TypedDict


# Use TypeAlias to be descriptive about what kinds of strings are expected.
# This retains the flexibility of str, avoiding the need for awkward casting.
PlatformSubdirStr: TypeAlias = str
PackageNameStr: TypeAlias = str


class HashableFakePackage(TypedDict):
    """A dict that represents a fake package when computing the lockfile content hash"""

    name: PackageNameStr
    version: str
    build_string: str
    build_number: int
    build: str
    noarch: str
    depends: List[str]
    timestamp: int
    package_type: Optional[str]
    subdir: PlatformSubdirStr


class SerializedDependency(TypedDict):
    # _BaseDependency fields:
    name: str
    manager: Literal["conda", "pip"]
    category: str
    extras: List[str]
    markers: NotRequired[Optional[str]]
    # Note, markers was added in conda-lock v3

    # VersionedDependency fields:
    version: NotRequired[str]
    build: NotRequired[Optional[str]]
    conda_channel: NotRequired[Optional[str]]
    hash: NotRequired[Optional[str]]

    # URLDependency fields:
    url: NotRequired[str]
    hashes: NotRequired[List[str]]

    # VCSDependency fields:
    source: NotRequired[str]
    vcs: NotRequired[str]
    rev: NotRequired[Optional[str]]
    subdirectory: NotRequired[Optional[str]]

    # PathDependency fields:
    path: NotRequired[str]
    is_directory: NotRequired[bool]
    # # Also in VCSDependency:
    # subdirectory: NotRequired[Optional[str]]


class RepoMetadataInfo(TypedDict):
    subdir: PlatformSubdirStr


class SubdirMetadata(TypedDict):
    info: RepoMetadataInfo
    packages: Dict[PackageNameStr, HashableFakePackage]


class EmptyDict(TypedDict):
    pass


HashableVirtualPackageRepresentation: "TypeAlias" = Dict[
    PlatformSubdirStr, Union[SubdirMetadata, EmptyDict]
]


class SerializedLockspec(TypedDict):
    channels: List[str]
    specs: List[SerializedDependency]
    pip_repositories: NotRequired[List[str]]
    virtual_package_hash: NotRequired[HashableVirtualPackageRepresentation]
