import re
import sys

from pathlib import Path
from typing import Dict, List, Optional, TypedDict
from urllib.parse import urldefrag

from clikit.api.io.flags import VERY_VERBOSE
from clikit.io import ConsoleIO, NullIO
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

from conda_lock import src_parser
from conda_lock.src_parser.pyproject_toml import get_lookup as get_forward_lookup
from conda_lock.src_parser.pyproject_toml import normalize_pypi_name


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

        if platform.startswith("osx-"):
            self._sys_platform = "darwin"
        elif platform.startswith("linux-"):
            self._sys_platform = "linux"
        elif platform.startswith("win-"):
            self._sys_platform = "win32"
        else:
            raise ValueError(f"Unsupported platform '{platform}'")

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

    def get_marker_env(self):
        return {
            # "implementation_name": implementation_name,
            # "implementation_version": iver,
            # "os_name": os.name,
            # "platform_machine": platform.machine(),
            # "platform_release": platform.release(),
            # "platform_system": platform.system(),
            # "platform_version": platform.version(),
            "python_full_version": ".".join([str(c) for c in self._python_version]),
            # "platform_python_implementation": platform.python_implementation(),
            "python_version": ".".join([str(c) for c in self._python_version[:2]]),
            "sys_platform": self._sys_platform,
            # "version_info": sys.version_info,
            # # Extra information
            # "interpreter_name": interpreter_name(),
            # "interpreter_version": interpreter_version(),
        }


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


def get_dependency(dep: src_parser.Dependency) -> Dependency:
    # FIXME: how do deal with extras?
    extras: List[str] = []
    if isinstance(dep, src_parser.VersionedDependency):
        return Dependency(
            name=dep.name, constraint=dep.version or "*", extras=dep.extras
        )
    elif isinstance(dep, src_parser.URLDependency):
        return URLDependency(name=dep.name, url=dep.url, extras=extras)
    else:
        raise ValueError(f"Unknown requirement {dep}")


def get_package(locked: src_parser.LockedDependency) -> Package:
    if locked.source is not None:
        return Package(
            locked.name,
            source_type="url",
            source_url=locked.source.url,
            version="0.0.0",
        )
    else:
        return Package(locked.name, version=locked.version)


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
    pip_specs: Dict[str, src_parser.Dependency],
    use_latest: List[str],
    pip_locked: Dict[str, src_parser.LockedDependency],
    conda_locked: Dict[str, src_parser.LockedDependency],
    python_version: str,
    platform: str,
    verbose: bool = False,
) -> Dict[str, src_parser.LockedDependency]:
    dummy_package = ProjectPackage("_dummy_package_", "0.0.0")
    dependencies = [get_dependency(spec) for spec in pip_specs.values()]
    for dep in dependencies:
        dummy_package.add_dependency(dep)

    pypi = PyPiRepository()
    pool = Pool(repositories=[pypi])

    installed = Repository()
    locked = Repository()

    python_packages = dict()
    for dep in conda_locked.values():
        if dep.name.startswith("__"):
            continue
        pypi_name = normalize_conda_name(dep.name)
        # Prefer the Python package when its name collides with the Conda package
        # for the underlying library, e.g. python-xxhash (pypi: xxhash) over xxhash
        # (pypi: no equivalent)
        if pypi_name not in python_packages or pypi_name != dep.name:
            python_packages[pypi_name] = dep.version
    # treat conda packages as both locked and installed
    for name, version in python_packages.items():
        for repo in (locked, installed):
            repo.add_package(Package(name=name, version=version))
    # treat pip packages as locked only
    for spec in pip_locked.values():
        locked.add_package(get_package(spec))

    if verbose:
        io = ConsoleIO()
        io.set_verbosity(VERY_VERBOSE)
    else:
        io = NullIO()
    s = Solver(
        dummy_package,
        pool=pool,
        installed=installed,
        locked=locked,
        io=io,
    )
    to_update = list(
        {spec.name for spec in pip_locked.values()}.intersection(use_latest)
    )
    env = PlatformEnv(python_version, platform)
    # find platform-specific solution (e.g. dependencies conditioned on markers)
    with s.use_environment(env):
        result = s.solve(use_latest=to_update)

    chooser = Chooser(pool, env=env)

    # Extract distributions from Poetry package plan, ignoring uninstalls
    # (usually: conda package with no pypi equivalent) and skipped ops
    # (already installed)
    requirements: List[src_parser.LockedDependency] = []
    for op in result:
        if not isinstance(op, Uninstall) and not op.skipped:
            # Take direct references verbatim
            source: Optional[src_parser.DependencySource] = None
            if op.package.source_type == "url":
                url, fragment = urldefrag(op.package.source_url)
                hash = fragment.replace("=", ":")
                source = src_parser.DependencySource(type="url", url=url)
            # Choose the most specific distribution for the target
            else:
                link = chooser.choose_for(op.package)
                url = link.url_without_fragment
                hash = f"{link.hash_name}:{link.hash}"

            requirements.append(
                src_parser.LockedDependency(
                    name=op.package.name,
                    version=str(op.package.version),
                    manager="pip",
                    source=source,
                    platform=platform,
                    dependencies={
                        dep.name: str(dep.constraint) for dep in op.package.requires
                    },
                    url=url,
                    hash=hash,
                )
            )

    # use PyPI names of conda packages to walking the dependency tree and propagate
    # categories from explicit to transitive dependencies
    planned = {
        **{
            normalize_conda_name(name).lower(): dep
            for name, dep in conda_locked.items()
        },
        **{dep.name: dep for dep in requirements},
    }

    src_parser._apply_categories(requested=pip_specs, planned=planned)

    return {dep.name: dep for dep in requirements}
