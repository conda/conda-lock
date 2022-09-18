import atexit
import json
import logging
import os
import pathlib

from collections import defaultdict
from types import TracebackType
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type

from pydantic import BaseModel, Field, validator

from conda_lock.models.channel import Channel


logger = logging.getLogger(__name__)

# datetime.datetime(2020, 1, 1).timestamp()
DEFAULT_TIME = 1577854800000


class FakePackage(BaseModel):
    """A minimal representation of the required metadata for a conda package"""

    class Config:
        allow_mutation = False
        frozen = True

    name: str
    version: str = "1.0"
    build_string: str = ""
    build_number: int = 0
    noarch: str = ""
    depends: Tuple[str, ...] = Field(default_factory=tuple)
    timestamp: int = DEFAULT_TIME
    package_type: Optional[str] = "virtual_system"

    def to_repodata_entry(self) -> Tuple[str, Dict[str, Any]]:
        out = self.dict()
        if self.build_string:
            build = f"{self.build_string}_{self.build_number}"
        else:
            build = f"{self.build_number}"
        out["depends"] = list(out["depends"])
        out["build"] = build
        fname = f"{self.name}-{self.version}-{build}.tar.bz2"
        return fname, out


class FakeRepoData(BaseModel):
    base_path: pathlib.Path
    packages_by_subdir: Dict[FakePackage, Set[str]] = Field(
        default_factory=lambda: defaultdict(set)
    )
    all_subdirs: Set[str] = {
        "noarch",
        "linux-aarch64",
        "linux-ppc64le",
        "linux-64",
        "osx-64",
        "osx-arm64",
        "win-64",
    }
    all_repodata: Dict[str, dict] = {}
    hash: Optional[str] = None
    old_env_vars: Dict[str, Optional[str]] = {}

    @property
    def channel_url(self) -> str:
        if isinstance(self.base_path, pathlib.WindowsPath):
            return str(self.base_path.absolute())
        else:
            return f"file://{self.base_path.absolute().as_posix()}"

    @property
    def channel(self) -> Channel:
        return Channel(url=self.channel_url, used_env_vars=frozenset([]))

    @property
    def channel_url_posix(self) -> str:
        if isinstance(self.base_path, pathlib.WindowsPath):
            # Mamba has a different return format for windows filepach urls and takes the form
            # file:///C:/dira/dirb
            return f"file:///{self.base_path.absolute().as_posix()}"
        else:
            return f"file://{self.base_path.absolute().as_posix()}"

    def add_package(self, package: FakePackage, subdirs: Iterable[str] = ()) -> None:
        subdirs = frozenset(subdirs)
        if not subdirs:
            subdirs = frozenset(["noarch"])
        self.packages_by_subdir[package].update(subdirs)

    def _write_subdir(self, subdir: str) -> dict:
        packages: dict = {}
        out = {"info": {"subdir": subdir}, "packages": packages}
        for pkg, subdirs in self.packages_by_subdir.items():
            if subdir not in subdirs:
                continue
            fname, info_dict = pkg.to_repodata_entry()
            info_dict["subdir"] = subdir
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
        exc_type: Optional[Type[BaseException]],
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


OSX_VERSIONS_X86 = ["10.15"]
OSX_VERSIONS_X68_ARM64 = ["11.0"]
OSX_VERSIONS_ARM64: List[str] = []


def default_virtual_package_repodata() -> FakeRepoData:
    """Define a reasonable modern set of virtual packages that should be safe enough to assume"""
    repodata = _init_fake_repodata()

    unix_virtual = FakePackage(name="__unix", version="0")
    repodata.add_package(
        unix_virtual,
        subdirs=["linux-aarch64", "linux-ppc64le", "linux-64", "osx-64", "osx-arm64"],
    )

    linux_virtual = FakePackage(name="__linux", version="5.10")
    repodata.add_package(
        linux_virtual, subdirs=["linux-aarch64", "linux-ppc64le", "linux-64"]
    )

    win_virtual = FakePackage(name="__win", version="0")
    repodata.add_package(win_virtual, subdirs=["win-64"])

    archspec_x86 = FakePackage(name="__archspec", version="1", build_string="x86_64")
    repodata.add_package(archspec_x86, subdirs=["win-64", "linux-64", "osx-64"])

    archspec_arm64 = FakePackage(name="__archspec", version="1", build_string="arm64")
    repodata.add_package(archspec_arm64, subdirs=["osx-arm64"])

    archspec_aarch64 = FakePackage(
        name="__archspec", version="1", build_string="aarch64"
    )
    repodata.add_package(archspec_aarch64, subdirs=["linux-aarch64"])

    archspec_ppc64le = FakePackage(
        name="__archspec", version="1", build_string="ppc64le"
    )
    repodata.add_package(archspec_ppc64le, subdirs=["linux-ppc64le"])

    glibc_virtual = FakePackage(name="__glibc", version="2.17")
    repodata.add_package(
        glibc_virtual, subdirs=["linux-aarch64", "linux-ppc64le", "linux-64"]
    )

    for cuda_version in ["11.4"]:
        cuda_virtual = FakePackage(name="__cuda", version=cuda_version)
        repodata.add_package(
            cuda_virtual,
            subdirs=["linux-aarch64", "linux-ppc64le", "linux-64", "win-64"],
        )

    for osx_ver in OSX_VERSIONS_X86:
        package = FakePackage(name="__osx", version=osx_ver)
        repodata.add_package(package, subdirs=["osx-64"])
    for osx_ver in OSX_VERSIONS_X68_ARM64:
        package = FakePackage(name="__osx", version=osx_ver)
        repodata.add_package(package, subdirs=["osx-64", "osx-arm64"])
    for osx_ver in OSX_VERSIONS_ARM64:
        package = FakePackage(name="__osx", version=osx_ver)
        repodata.add_package(package, subdirs=["osx-arm64"])
    repodata.write()
    return repodata


class VirtualPackageSpecSubdir(BaseModel):
    packages: Dict[str, str]

    @validator("packages")
    def validate_packages(cls, v: Dict[str, str]) -> Dict[str, str]:
        for package_name in v:
            if not package_name.startswith("__"):
                raise ValueError(f"{package_name} is not a virtual package!")
        return v


class VirtualPackageSpec(BaseModel):
    subdirs: Dict[str, VirtualPackageSpecSubdir]


def virtual_package_repo_from_specification(
    virtual_package_spec_file: pathlib.Path,
) -> FakeRepoData:
    import yaml

    with virtual_package_spec_file.open("r") as fp:
        data = yaml.safe_load(fp)
    logging.debug("Virtual package spec: %s", data)

    spec = VirtualPackageSpec.parse_obj(data)

    repodata = _init_fake_repodata()
    for subdir, subdir_spec in spec.subdirs.items():
        for virtual_package, version in subdir_spec.packages.items():
            repodata.add_package(
                FakePackage(name=virtual_package, version=version), subdirs=[subdir]
            )
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
