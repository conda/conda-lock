import re
import sys

from pathlib import Path
from typing import Dict, List, Optional, TypedDict
from urllib.parse import urldefrag

from clikit.api.io.flags import VERY_VERBOSE
from clikit.io import ConsoleIO
from packaging.tags import compatible_tags, cpython_tags
from poetry.core.packages import Dependency, Package, ProjectPackage, URLDependency
from poetry.installation.chooser import Chooser
from poetry.installation.operations import Install
from poetry.installation.operations.uninstall import Uninstall
from poetry.puzzle import Solver
from poetry.repositories.pool import Pool
from poetry.repositories.pypi_repository import PyPiRepository
from poetry.repositories.repository import Repository
from poetry.utils.env import Env

from conda_lock.src_parser import PipPackage
from conda_lock.src_parser.pyproject_toml import get_lookup as get_forward_lookup


class PlatformEnv(Env):
    def __init__(self, python_version, platform):
        super().__init__(path=Path(sys.prefix))
        if platform == "linux-64":
            # FIXME: in principle these depend on the glibc in the conda env
            self._platforms = ["manylinux_2_17_x86_64", "manylinux2014_x86_64"]
        elif platform == "osx-64":
            self._platforms = ["macosx_10_9_x86_64"]
        elif platform == "win-64":
            self._platforms = ["win_amd64"]
        else:
            raise ValueError(f"Unsupported platform '{platform}'")
        self._python_version = tuple(map(int, python_version.split(".")))

    def get_supported_tags(self):
        """
        Mimic the output of packaging.tags.sys_tags() on the given platform
        """
        return list(
            cpython_tags(python_version=self._python_version, platforms=self._platforms)
        ) + list(
            compatible_tags(
                python_version=self._python_version, platforms=self._platforms
            )
        )


REQUIREMENT_PATTERN = re.compile(
    r"""
    ^
    (?P<name>[a-zA-Z0-9_-]+) # package name
    (?:\[(?P<extras>(?:\s?[a-zA-Z0-9_-]+(?:\s?\,\s?)?)+)\])? # extras
    (?:
        (?: # a direct reference
            \s?@\s?(?P<url>.*)
        )
        |
        (?: # one or more PEP440 version specifiers
            \s?(?P<constraint>
                (?:\s?
                    (?:
                        (?:=|[><~=!])?=
                        |
                        [<>]
                    )?
                    \s?
                    (?:
                        [A-Za-z0-9\.-_\*]+ # a version tuple, e.g. x.y.z
                        (?:-[A-Za-z]+(?:\.[0-9]+)?)? # a post-release tag, e.g. -alpha.2
                        (?:\s?\,\s?)?
                    )
                )+
            )
        )
    )?
    $
    """,
    re.VERBOSE,
)


def parse_pip_requirement(requirement: str) -> Optional[Dict[str, str]]:
    match = REQUIREMENT_PATTERN.match(requirement)
    if not match:
        return None
    return match.groupdict()


def get_dependency(requirement: str) -> Dependency:
    parsed = parse_pip_requirement(requirement)
    if parsed is None:
        raise ValueError(f"Unknown pip requirement '{requirement}'")
    extras = re.split(r"\s?\,\s?", parsed["extras"]) if parsed["extras"] else None
    if parsed["url"]:
        return URLDependency(name=parsed["name"], url=parsed["url"], extras=extras)
    else:
        return Dependency(
            name=parsed["name"], constraint=parsed["constraint"] or "*", extras=extras
        )


def get_package(locked: PipPackage) -> Package:
    if locked["version"] is None:
        return Package(
            locked["name"], source_type="url", source_url=locked["url"], version="0.0.0"
        )
    else:
        return Package(locked["name"], version=locked["version"])


PYPI_LOOKUP: Optional[Dict] = None


def get_lookup() -> Dict:
    global PYPI_LOOKUP
    if PYPI_LOOKUP is None:
        PYPI_LOOKUP = {
            record["conda_name"]: record for record in get_forward_lookup().values()
        }
    return PYPI_LOOKUP


def normalize_conda_name(name: str):
    return get_lookup().get(name, {"pypi_name": name})["pypi_name"]


def solve_pypi(
    pip_specs: List[str],
    use_latest: List[str],
    pip_locked: List[PipPackage],
    conda_locked: List[tuple[str, str]],
    python_version: str,
    platform: str,
    verbose: bool = False,
) -> List[PipPackage]:
    dummy_package = ProjectPackage("_dummy_package_", "0.0.0")
    dummy_package.python_versions = f"=={python_version}"
    dependencies = [get_dependency(spec) for spec in pip_specs]
    for dep in dependencies:
        dummy_package.add_dependency(dep)

    pypi = PyPiRepository()
    pool = Pool(repositories=[pypi])

    installed = Repository()
    locked = Repository()

    python_packages = dict()
    for name, version in conda_locked:
        pypi_name = normalize_conda_name(name)
        # Prefer the Python package when its name collides with the Conda package
        # for the underlying library, e.g. python-xxhash (pypi: xxhash) over xxhash
        # (pypi: no equivalent)
        if pypi_name not in python_packages or pypi_name != name:
            python_packages[pypi_name] = version
    # treat conda packages as both locked and installed
    for name, version in python_packages.items():
        for repo in (locked, installed):
            repo.add_package(Package(name=name, version=version))
    # treat pip packages as locked only
    for spec in pip_locked:
        locked.add_package(get_package(spec))

    io = ConsoleIO()
    if verbose:
        io.set_verbosity(VERY_VERBOSE)
    s = Solver(
        dummy_package,
        pool=pool,
        installed=installed,
        locked=locked,
        io=io,
    )
    to_update = list({spec["name"] for spec in pip_locked}.intersection(use_latest))
    result = s.solve(use_latest=to_update)

    chooser = Chooser(pool, env=PlatformEnv(python_version, platform))

    # Extract distributions from Poetry package plan, ignoring uninstalls
    # (usually: conda package with no pypi equivalent) and skipped ops
    # (already installed)
    requirements: List[PipPackage] = []
    for op in result:
        if not isinstance(op, Uninstall) and not op.skipped:
            # Take direct references verbatim
            if op.package.source_type == "url":
                url, fragment = urldefrag(op.package.source_url)
                requirements.append(
                    {
                        "name": op.package.name,
                        "version": None,
                        "url": url,
                        "hashes": [fragment.replace("=", ":")],
                    }
                )
            # Choose the most specific distribution for the target
            else:
                link = chooser.choose_for(op.package)
                requirements.append(
                    {
                        "name": op.package.name,
                        "version": str(op.package.version),
                        "url": link.url_without_fragment,
                        "hashes": [f"{link.hash_name}:{link.hash}"],
                    }
                )

    return requirements
