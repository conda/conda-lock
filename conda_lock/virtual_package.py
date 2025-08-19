import atexit
import json
import logging
import os
import pathlib

from collections import defaultdict
from collections.abc import Iterable
from importlib.resources import path
from types import TracebackType
from typing import (
    Literal,
    Optional,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing_extensions import TypeAlias

from conda_lock.content_hash_types import (
    HashableVirtualPackage,
    HashableVirtualPackageRepresentation,
    PackageNameStr,
    PlatformSubdirStr,
    SubdirMetadata,
)
from conda_lock.models.channel import Channel


logger = logging.getLogger(__name__)

# datetime.datetime(2020, 1, 1).timestamp()
DEFAULT_TIME = 1577854800000

VirtualPackageVersion: TypeAlias = str


with path("conda_lock", "default-virtual-packages.yaml") as p:
    DEFAULT_VIRTUAL_PACKAGES_YAML_PATH = p


class VirtualPackage(BaseModel):
    """A minimal representation of the required metadata for a virtual package.

    This is used by our specification. It's then converted to a FullVirtualPackage
    for computing the content hash. Then it's converted to a dict to be used in
    repodata.json.
    """

    model_config = ConfigDict(frozen=True)

    name: PackageNameStr
    version: VirtualPackageVersion
    build_string: str = ""

    def to_full_virtual_package(self) -> "FullVirtualPackage":
        return FullVirtualPackage(
            name=self.name,
            version=self.version,
            build_string=self.build_string,
        )

    def to_repodata_entry(
        self, *, subdir: PlatformSubdirStr
    ) -> tuple[str, HashableVirtualPackage]:
        p = self.to_full_virtual_package()
        out: HashableVirtualPackage = {
            "name": p.name,
            "version": p.version,
            "build_string": p.build_string,
            "build_number": p.build_number,
            "noarch": p.noarch,
            "depends": list(p.depends),
            "timestamp": p.timestamp,
            "package_type": p.package_type,
            "build": p.build,
            "subdir": subdir,
        }
        fname = f"{self.name}-{self.version}-{p.build}.tar.bz2"
        return fname, out


class FullVirtualPackage(VirtualPackage):
    """Everything necessary for repodata.json except subdir"""

    build_number: int = 0
    noarch: str = ""
    depends: tuple[str, ...] = Field(default_factory=tuple)
    timestamp: int = DEFAULT_TIME
    package_type: Optional[str] = "virtual_system"

    @property
    def build(self) -> str:
        if self.build_string:
            return self.build_string
        else:
            return str(self.build_number)


class FakeRepoData(BaseModel):
    base_path: pathlib.Path
    packages_by_subdir: defaultdict[FullVirtualPackage, set[PackageNameStr]] = Field(
        default_factory=lambda: defaultdict(set)  # type: ignore[arg-type,unused-ignore]
    )
    all_subdirs: set[PlatformSubdirStr] = {
        "noarch",
        "linux-aarch64",
        "linux-ppc64le",
        "linux-64",
        "osx-64",
        "osx-arm64",
        "win-64",
    }
    all_repodata: HashableVirtualPackageRepresentation = {}
    hash: Optional[str] = None
    old_env_vars: dict[str, Optional[str]] = {}

    @property
    def channel_url(self) -> str:
        if isinstance(self.base_path, pathlib.WindowsPath):
            return str(self.base_path.absolute())
        else:
            return f"file://{self.base_path.absolute().as_posix()}"

    @property
    def channel(self) -> Channel:
        # The URL is a file path, so there are no env vars. Thus we use the
        # raw Channel constructor here rather than the usual Channel.from_string().
        return Channel(url=self.channel_url, used_env_vars=())

    @property
    def channel_url_posix(self) -> str:
        if isinstance(self.base_path, pathlib.WindowsPath):
            # Mamba has a different return format for windows filepach urls and takes the form
            # file:///C:/dira/dirb
            return f"file:///{self.base_path.absolute().as_posix()}"
        else:
            return f"file://{self.base_path.absolute().as_posix()}"

    def add_package(
        self, package: VirtualPackage, subdirs: Iterable[PlatformSubdirStr] = ()
    ) -> None:
        subdirs = frozenset(subdirs)
        if not subdirs:
            subdirs = frozenset(["noarch"])
        self.packages_by_subdir[package.to_full_virtual_package()].update(subdirs)

    def _write_subdir(self, subdir: PlatformSubdirStr) -> SubdirMetadata:
        packages: dict[PackageNameStr, HashableVirtualPackage] = {}
        out: SubdirMetadata = {"info": {"subdir": subdir}, "packages": packages}
        for pkg, subdirs in self.packages_by_subdir.items():
            if subdir not in subdirs:
                continue
            fname, info_dict = pkg.to_repodata_entry(subdir=subdir)
            packages[fname] = info_dict

        (self.base_path / subdir).mkdir(exist_ok=True)
        content = json.dumps(out, sort_keys=True)
        (self.base_path / subdir / "repodata.json").write_text(content)
        return out

    def write(self) -> None:
        for subdirs in self.packages_by_subdir.values():
            self.all_subdirs.update(subdirs)

        for subdir in sorted(self.all_subdirs):
            repodata = self._write_subdir(subdir)
            self.all_repodata[subdir] = repodata

        logger.debug("Wrote fake repodata to %s", self.base_path)
        import glob

        for filename in glob.iglob(str(self.base_path / "**"), recursive=True):
            logger.debug(filename)
        logger.debug("repo: %s", self.channel_url)

    def __enter__(self) -> None:
        """Ensure that if glibc etc is set by the overrides we force the conda solver override variables"""
        env_vars_to_clear = set()
        for package in self.packages_by_subdir:
            if package.name.startswith("__"):
                upper_name = package.name.lstrip("_").upper()
                env_vars_to_clear.add(f"CONDA_OVERRIDE_{upper_name}")

        for e in env_vars_to_clear:
            self.old_env_vars[e] = os.environ.get(e)
            os.environ[e] = ""

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Clear out old vars"""
        for k, v in self.old_env_vars.items():
            if v is None:
                del os.environ[k]
            else:
                os.environ[k] = v


def _init_fake_repodata() -> FakeRepoData:
    import shutil
    import tempfile

    # tmp directory in github actions
    runner_tmp = os.environ.get("RUNNER_TEMP")
    tmp_dir = tempfile.mkdtemp(dir=runner_tmp)

    if not runner_tmp:
        # no need to bother cleaning up on CI
        def clean() -> None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        atexit.register(clean)

    tmp_path = pathlib.Path(tmp_dir)
    repodata = FakeRepoData(base_path=tmp_path)
    return repodata


def default_virtual_package_repodata(
    cuda_version: Union[Literal["default", ""], VirtualPackageVersion] = "default",
) -> FakeRepoData:
    """An empty cuda_version indicates that CUDA is unavailable."""
    """Define a reasonable modern set of virtual packages that should be safe enough to assume"""
    repodata = virtual_package_repo_from_specification(
        DEFAULT_VIRTUAL_PACKAGES_YAML_PATH,
        override_cuda_version=cuda_version,
        add_duplicate_osx_package=True,
    )
    return repodata


class VirtualPackageSpecSubdir(BaseModel):
    """Virtual packages for a specific subdir in a virtual-packages.yaml file"""

    packages: dict[PackageNameStr, VirtualPackageVersion]

    @field_validator("packages")
    @classmethod
    def validate_packages(
        cls, v: dict[PackageNameStr, VirtualPackageVersion]
    ) -> dict[PackageNameStr, VirtualPackageVersion]:
        for package_name in v:
            if not package_name.startswith("__"):
                raise ValueError(f"{package_name} is not a virtual package!")
        return v


class VirtualPackageSpec(BaseModel):
    """Virtual packages specified in a virtual-packages.yaml file"""

    subdirs: dict[PlatformSubdirStr, VirtualPackageSpecSubdir]


def _parse_virtual_package_spec(
    virtual_package_name: PackageNameStr, version_spec: str
) -> VirtualPackage:
    """Parse a virtual package specification into a VirtualPackage object.

    Args:
        virtual_package_name: The name of the virtual package (should start with '__')
        version_spec: The version specification string, optionally including a build string
            separated by a space

    Returns:
        A VirtualPackage object with the parsed name, version, and build string

    Examples:
        >>> _parse_virtual_package_spec("__unix", "0")
        VirtualPackage(name='__unix', version='0', build_string='')
        >>> _parse_virtual_package_spec("__archspec", "1 x86_64")
        VirtualPackage(name='__archspec', version='1', build_string='x86_64')
    """
    version_parts = version_spec.split(" ", 1)
    assert len(version_parts) in (1, 2)
    if len(version_parts) == 1:
        parsed_version, build_string = version_spec, ""
    else:
        parsed_version, build_string = version_parts
    return VirtualPackage(
        name=virtual_package_name,
        version=parsed_version,
        build_string=build_string,
    )


def virtual_package_repo_from_specification(
    virtual_package_spec_file: pathlib.Path,
    add_duplicate_osx_package: bool = False,
    override_cuda_version: Union[
        Literal["default", ""], VirtualPackageVersion
    ] = "default",
) -> FakeRepoData:
    import yaml

    with virtual_package_spec_file.open("r") as fp:
        data = yaml.safe_load(fp)
    logging.debug("Virtual package spec: %s", data)

    virtual_package_spec = VirtualPackageSpec.model_validate(data)

    repodata = _init_fake_repodata()
    for subdir, subdir_spec in virtual_package_spec.subdirs.items():
        for virtual_package_name, version_spec in subdir_spec.packages.items():
            # Override the CUDA version if specified.
            if virtual_package_name == "__cuda" and override_cuda_version != "default":
                if override_cuda_version == "":
                    continue
                version_spec = override_cuda_version

            virtual_package = _parse_virtual_package_spec(
                virtual_package_name, version_spec
            )
            repodata.add_package(virtual_package, subdirs=[subdir])

    if add_duplicate_osx_package:
        # This is to preserve exact consistency with previous versions of conda-lock.
        # Previous versions of conda-lock would add the __osx package twice, once with
        # version "10.15" and once with version "11.0". The package with version "10.15"
        # is ignored by conda and mamba, but is still present in the virtual repodata,
        # and contributes to the content hash.
        # <https://github.com/conda/conda-lock/blob/f5323e7a71259ed17173401ec4cd728c6d161fe1/conda_lock/virtual_package.py#L191-L252>
        package = VirtualPackage(name="__osx", version="10.15")
        repodata.add_package(package, subdirs=["osx-64"])

    repodata.write()
    return repodata


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    fil = (
        pathlib.Path(__file__).parent.parent
        / "tests"
        / "test-cuda"
        / "virtual-packages.yaml"
    )
    rd = virtual_package_repo_from_specification(fil)
    print(rd)
    print((rd.base_path / "linux-64" / "repodata.json").read_text())
