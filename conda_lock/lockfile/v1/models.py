import datetime
import enum
import hashlib
import json
import logging
import pathlib

from collections import namedtuple
from collections.abc import Set
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Optional,
    Union,
)


if TYPE_CHECKING:
    from hashlib import _Hash

from pathlib import PurePosixPath
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import Field, ValidationInfo, field_validator
from typing_extensions import Literal

from conda_lock.common import ordered_union, relative_path
from conda_lock.models import StrictModel
from conda_lock.models.channel import Channel
from conda_lock.models.dry_run_install import FetchAction


logger = logging.getLogger(__name__)


class DependencySource(StrictModel):
    type: Literal["url"]
    url: str


LockKey = namedtuple("LockKey", ["manager", "name", "platform"])


class HashModel(StrictModel):
    md5: Optional[str] = None
    sha256: Optional[str] = None


class BaseLockedDependency(StrictModel):
    name: str
    version: str
    manager: Literal["conda", "pip"]
    platform: str
    dependencies: dict[str, str] = {}
    url: str
    hash: HashModel
    source: Optional[DependencySource] = None
    build: Optional[str] = None

    def key(self) -> LockKey:
        return LockKey(self.manager, self.name, self.platform)

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, v: HashModel, info: ValidationInfo) -> HashModel:
        if (info.data["manager"] == "conda") and (v.md5 is None):
            raise ValueError("conda package hashes must use MD5")
        return v

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v:
            raise ValueError("URL cannot be empty")
        return v

    def to_fetch_action(self) -> FetchAction:
        """
        Build a FETCH action from this LockedDependency.

        This method is used to create a representation of a conda package
        that can be used by the installer.

        This is particularly useful during a lockfile update (`--update`).
        For packages that are not being updated, conda-lock determines their
        details by inspecting the state of the solver's fake environment
        (e.g., via `conda list --json`). However, this inspection does not
        reveal the original package's file extension (`.conda` vs. `.tar.bz2`),
        as that information is not stored in the installed package metadata.

        By using this method on an existing `LockedDependency` from the old
        lockfile, we can use the stored `url` to accurately preserve the
        original filename and extension.
        """
        if self.manager != "conda":
            raise ValueError("Only conda packages can be converted to FETCH actions")
        if self.hash.md5 is None:
            raise RuntimeError(
                "Conda packages are already validated to have an MD5 hash"
            )
        # Parse the stored URL
        parts = urlsplit(self.url)
        path = PurePosixPath(parts.path)

        # platform directory is the parent of the filename
        parsed_platform = path.parent.name  # e.g. "linux-64"
        # Note that self.platform is the concrete target platform.
        # Noarch packages can be installed on any platform, so such
        # a mismatch is no contradiction.
        if self.platform != parsed_platform and parsed_platform != "noarch":
            raise ValueError(
                f"Platform mismatch for package {self.name} {self.version}. "
                f"Expected '{self.platform}' but URL '{self.url}' contains '{parsed_platform}'."
            )
        filename_with_extension = path.name  # e.g. "tzdata-2022g-h191b570_0.conda"

        # base_url is everything up to the platform directory
        base_url_path = str(path.parent.parent)  # e.g. "/conda-forge"
        base_url_parts = SplitResult(
            scheme=parts.scheme,  # e.g. "https"
            netloc=parts.netloc,  # e.g. "user:pass@conda.anaconda.org"
            path=base_url_path,  # e.g. "/conda-forge"
            query="",
            fragment="",
        )
        base_url = urlunsplit(
            base_url_parts
        )  # e.g. "https://user:pass@conda.anaconda.org/conda-forge"

        channel_url = f"{base_url}/{self.platform}"  # e.g. "https://user:pass@conda.anaconda.org/conda-forge/linux-64"

        fetch_action = FetchAction(
            name=self.name,
            version=self.version,
            channel=channel_url,
            url=self.url,
            fn=filename_with_extension,
            md5=self.hash.md5,
            sha256=self.hash.sha256,
            depends=[f"{k} {v}".strip() for k, v in self.dependencies.items()],
            constrains=[],
            subdir=self.platform,
            timestamp=0,
        )
        return fetch_action


class LockedDependency(BaseLockedDependency):
    category: str = "main"
    optional: bool


class MetadataOption(enum.Enum):
    TimeStamp = "timestamp"
    GitSha = "git_sha"
    GitUserName = "git_user_name"
    GitUserEmail = "git_user_email"
    InputMd5 = "input_md5"
    InputSha = "input_sha"


class TimeMeta(StrictModel):
    """Stores information about when the lockfile was generated."""

    created_at: str = Field(..., description="Time stamp of lock-file creation time")

    @classmethod
    def create(cls) -> "TimeMeta":
        return cls(
            created_at=datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        )


class GitMeta(StrictModel):
    """
    Stores information about the git repo the lockfile is being generated in (if applicable) and
    the git user generating the file.
    """

    git_user_name: Optional[str] = Field(
        default=None, description="Git user.name field of global config"
    )
    git_user_email: Optional[str] = Field(
        default=None, description="Git user.email field of global config"
    )
    git_sha: Optional[str] = Field(
        default=None,
        description=(
            "sha256 hash of the most recent git commit that modified one of the input files for "
            + "this lockfile"
        ),
    )

    @classmethod
    def create(
        cls,
        metadata_choices: Set[MetadataOption],
        src_files: list[pathlib.Path],
    ) -> "GitMeta | None":
        try:
            import git
            import git.exc
        except ImportError:
            return None

        git_sha: Optional[str] = None
        git_user_name: Optional[str] = None
        git_user_email: Optional[str] = None

        try:
            repo = git.Repo(search_parent_directories=True)
            if MetadataOption.GitSha in metadata_choices:
                most_recent_datetime: Optional[datetime.datetime] = None
                for src_file in src_files:
                    relative_src_file_path = relative_path(
                        pathlib.Path(repo.working_tree_dir),  # type: ignore
                        src_file,
                    )
                    commit = list(
                        repo.iter_commits(paths=relative_src_file_path, max_count=1)
                    )[0]
                    if repo.is_dirty(path=relative_src_file_path):
                        logger.warning(
                            "One of the inputs to conda-lock is dirty, using commit hash of head +"
                            ' "dirty"'
                        )
                        git_sha = f"{repo.head.object.hexsha}-dirty"
                        break
                    else:
                        if (
                            most_recent_datetime is None
                            or most_recent_datetime < commit.committed_datetime
                        ):
                            most_recent_datetime = commit.committed_datetime
                            git_sha = commit.hexsha
            if MetadataOption.GitUserName in metadata_choices:
                git_user_name = repo.config_reader().get_value("user", "name", None)  # type: ignore
            if MetadataOption.GitUserEmail in metadata_choices:
                git_user_email = repo.config_reader().get_value("user", "email", None)  # type: ignore
        except git.exc.InvalidGitRepositoryError:
            pass

        if any([git_sha, git_user_name, git_user_email]):
            return cls(
                git_sha=git_sha,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
            )
        else:
            return None


class InputMeta(StrictModel):
    """Stores information about an input provided to generate the lockfile."""

    md5: Optional[str] = Field(..., description="md5 checksum for an input file")
    sha256: Optional[str] = Field(..., description="md5 checksum for an input file")

    @classmethod
    def create(
        cls, metadata_choices: Set[MetadataOption], src_file: pathlib.Path
    ) -> "InputMeta":
        if MetadataOption.InputSha in metadata_choices:
            sha256 = cls.get_input_sha256(src_file=src_file)
        else:
            sha256 = None
        if MetadataOption.InputMd5 in metadata_choices:
            md5 = cls.get_input_md5(src_file=src_file)
        else:
            md5 = None
        return cls(
            md5=md5,
            sha256=sha256,
        )

    @classmethod
    def get_input_md5(cls, src_file: pathlib.Path) -> str:
        hasher = hashlib.md5()
        return cls.hash_file(src_file=src_file, hasher=hasher)

    @classmethod
    def get_input_sha256(cls, src_file: pathlib.Path) -> str:
        hasher = hashlib.sha256()
        return cls.hash_file(src_file=src_file, hasher=hasher)

    @staticmethod
    def hash_file(src_file: pathlib.Path, hasher: "_Hash") -> str:
        with src_file.open("r") as infile:
            hasher.update(infile.read().encode("utf-8"))
        return hasher.hexdigest()


class LockMeta(StrictModel):
    content_hash: dict[str, str] = Field(
        ..., description="Hash of dependencies for each target platform"
    )
    channels: list[Channel] = Field(
        ..., description="Channels used to resolve dependencies", validate_default=True
    )
    platforms: list[str] = Field(..., description="Target platforms")
    sources: list[str] = Field(
        ...,
        description="paths to source files, relative to the parent directory of the lockfile",
    )
    time_metadata: Optional[TimeMeta] = Field(
        default=None, description="Metadata dealing with the time lockfile was created"
    )
    git_metadata: Optional[GitMeta] = Field(
        default=None,
        description=(
            "Metadata dealing with the git repo the lockfile was created in and the user that created it"
        ),
    )
    inputs_metadata: Optional[dict[str, InputMeta]] = Field(
        default=None,
        description="Metadata dealing with the input files used to create the lockfile",
    )
    custom_metadata: Optional[dict[str, str]] = Field(
        default=None,
        description="Custom metadata provided by the user to be added to the lockfile",
    )

    def __or__(self, other: "LockMeta") -> "LockMeta":
        """merge other into self"""
        if other is None:
            return self
        elif not isinstance(other, LockMeta):
            raise TypeError

        if self.inputs_metadata is None:
            new_inputs_metadata = other.inputs_metadata
        elif other.inputs_metadata is None:
            new_inputs_metadata = self.inputs_metadata
        else:
            new_inputs_metadata = self.inputs_metadata
            new_inputs_metadata.update(other.inputs_metadata)

        if self.custom_metadata is None:
            new_custom_metadata = other.custom_metadata
        elif other.custom_metadata is None:
            new_custom_metadata = self.custom_metadata
        else:
            new_custom_metadata = self.custom_metadata
            for key in other.custom_metadata:
                if key in new_custom_metadata:
                    logger.warning(
                        f"Custom metadata key {key} provided twice, overwriting original value"
                        + f"({new_custom_metadata[key]}) with new value "
                        + f"({other.custom_metadata[key]})"
                    )
            new_custom_metadata.update(other.custom_metadata)
        return LockMeta(
            content_hash={**self.content_hash, **other.content_hash},
            channels=self.channels,
            platforms=sorted(set(self.platforms).union(other.platforms)),
            sources=ordered_union([self.sources, other.sources]),
            time_metadata=other.time_metadata,
            git_metadata=other.git_metadata,
            inputs_metadata=new_inputs_metadata,
            custom_metadata=new_custom_metadata,
        )

    @field_validator("channels", mode="before")
    @classmethod
    def ensure_channels(cls, v: list[Union[str, Channel]]) -> list[Channel]:
        res: list[Channel] = []
        for e in v:
            if isinstance(e, str):
                res.append(Channel.from_string(e))
            else:
                res.append(e)
        return res


class Lockfile(StrictModel):
    version: ClassVar[int] = 1

    package: list[LockedDependency]
    metadata: LockMeta

    def dict_for_output(self) -> dict[str, Any]:
        """Convert the lockfile to a dictionary that can be written to a file."""
        return {
            "version": Lockfile.version,
            "metadata": json.loads(
                self.metadata.model_dump_json(
                    by_alias=True, exclude_unset=True, exclude_none=True
                )
            ),
            "package": [
                package.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)
                for package in self.package
            ],
        }
