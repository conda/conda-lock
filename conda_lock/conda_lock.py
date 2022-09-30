"""
Somewhat hacky solution to create conda lock files.
"""

import datetime
import logging
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import tempfile

from contextlib import contextmanager
from functools import partial
from types import TracebackType
from typing import (
    AbstractSet,
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)
from urllib.parse import urlsplit

import click
import pkg_resources
import yaml

from ensureconda import ensureconda
from typing_extensions import Literal

from conda_lock.click_helpers import OrderedGroup
from conda_lock.common import (
    read_file,
    read_json,
    relative_path,
    temporary_file_with_contents,
    write_file,
)
from conda_lock.conda_solver import solve_conda
from conda_lock.errors import MissingEnvVarError, PlatformValidationError
from conda_lock.invoke_conda import (
    PathLike,
    _invoke_conda,
    determine_conda_executable,
    is_micromamba,
)
from conda_lock.models.channel import Channel


try:
    from conda_lock.pypi_solver import solve_pypi

    PIP_SUPPORT = True
except ImportError:
    PIP_SUPPORT = False
from conda_lock.lookup import set_lookup_location
from conda_lock.src_parser import (
    Dependency,
    LockedDependency,
    Lockfile,
    LockMeta,
    LockSpecification,
    UpdateSpecification,
    aggregate_lock_specs,
)
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.lockfile import parse_conda_lock_file, write_conda_lock_file
from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml
from conda_lock.virtual_package import (
    FakeRepoData,
    default_virtual_package_repodata,
    virtual_package_repo_from_specification,
)


logger = logging.getLogger(__name__)
DEFAULT_FILES = [pathlib.Path("environment.yml")]

# Captures basic auth credentials, if they exists, in the second capture group.
AUTH_PATTERN = re.compile(r"^(https?:\/\/)(.*:.*@)?(.*)")

# Captures the domain in the second group.
DOMAIN_PATTERN = re.compile(r"^(https?:\/\/)?([^\/]+)(.*)")

# Captures the platform in the first group.
PLATFORM_PATTERN = re.compile(r"^# platform: (.*)$")
INPUT_HASH_PATTERN = re.compile(r"^# input_hash: (.*)$")


HAVE_MAMBA = (
    ensureconda(
        mamba=True, micromamba=False, conda=False, conda_exe=False, no_install=True
    )
    is not None
)


if not (sys.version_info.major >= 3 and sys.version_info.minor >= 6):
    print("conda_lock needs to run under python >=3.6")
    sys.exit(1)


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]

KIND_EXPLICIT: Literal["explicit"] = "explicit"
KIND_LOCK: Literal["lock"] = "lock"
KIND_ENV: Literal["env"] = "env"
TKindAll = Union[Literal["explicit"], Literal["lock"], Literal["env"]]
TKindRendarable = Union[Literal["explicit"], Literal["lock"], Literal["env"]]


DEFAULT_KINDS: List[Union[Literal["explicit"], Literal["lock"]]] = [
    KIND_EXPLICIT,
    KIND_LOCK,
]
DEFAULT_LOCKFILE_NAME = "conda-lock.yml"
KIND_FILE_EXT = {
    KIND_EXPLICIT: "",
    KIND_ENV: ".yml",
    KIND_LOCK: "." + DEFAULT_LOCKFILE_NAME,
}
KIND_USE_TEXT = {
    KIND_EXPLICIT: "conda create --name YOURENV --file {lockfile}",
    KIND_ENV: "conda env create --name YOURENV --file {lockfile}",
    KIND_LOCK: "conda-lock install --name YOURENV {lockfile}",
}


def _extract_platform(line: str) -> Optional[str]:
    search = PLATFORM_PATTERN.search(line)
    if search:
        return search.group(1)
    return None


def _extract_spec_hash(line: str) -> Optional[str]:
    search = INPUT_HASH_PATTERN.search(line)
    if search:
        return search.group(1)
    return None


def extract_platform(lockfile: str) -> str:
    for line in lockfile.strip().split("\n"):
        platform = _extract_platform(line)
        if platform:
            return platform
    raise RuntimeError("Cannot find platform in lockfile.")


def extract_input_hash(lockfile_contents: str) -> Optional[str]:
    for line in lockfile_contents.strip().split("\n"):
        platform = _extract_spec_hash(line)
        if platform:
            return platform
    return None


def _do_validate_platform(platform: str) -> Tuple[bool, str]:
    from ensureconda.resolve import platform_subdir

    determined_subdir = platform_subdir()
    return platform == determined_subdir, determined_subdir


def do_validate_platform(lockfile: str) -> None:
    platform_lockfile = extract_platform(lockfile)
    try:
        success, platform_sys = _do_validate_platform(platform_lockfile)
    except KeyError:
        raise RuntimeError(f"Unknown platform type in lockfile '{platform_lockfile}'.")
    if not success:
        raise PlatformValidationError(
            f"Platform in lockfile '{platform_lockfile}' is not compatible with system platform '{platform_sys}'."
        )


def do_conda_install(
    conda: PathLike, prefix: str, name: str, file: pathlib.Path
) -> None:

    _conda = partial(_invoke_conda, conda, prefix, name, check_call=True)

    kind = "env" if file.name.endswith(".yml") else "explicit"

    if kind == "explicit":
        with open(file) as explicit_env:
            pip_requirements = [
                line.split("# pip ")[1]
                for line in explicit_env
                if line.startswith("# pip ")
            ]
    else:
        pip_requirements = []

    _conda(
        [
            *(["env"] if kind == "env" and not is_micromamba(conda) else []),
            "create",
            "--file",
            str(file),
            *([] if kind == "env" else ["--yes"]),
        ],
    )

    if not pip_requirements:
        return

    with temporary_file_with_contents("\n".join(pip_requirements)) as requirements_path:
        _conda(["run"], ["pip", "install", "--no-deps", "-r", str(requirements_path)])


def fn_to_dist_name(fn: str) -> str:
    if fn.endswith(".conda"):
        fn, _, _ = fn.partition(".conda")
    elif fn.endswith(".tar.bz2"):
        fn, _, _ = fn.partition(".tar.bz2")
    else:
        raise RuntimeError(f"unexpected file type {fn}", fn)
    return fn


def make_lock_spec(
    *,
    src_files: List[pathlib.Path],
    virtual_package_repo: FakeRepoData,
    channel_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    required_categories: Optional[AbstractSet[str]] = None,
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files.  If required_categories is set filter out specs that do not match those"""
    lock_specs = parse_source_files(
        src_files=src_files, platform_overrides=platform_overrides or DEFAULT_PLATFORMS
    )

    lock_spec = aggregate_lock_specs(lock_specs)
    lock_spec.virtual_package_repo = virtual_package_repo
    lock_spec.channels = (
        [Channel.from_string(co) for co in channel_overrides]
        if channel_overrides
        else lock_spec.channels
    )
    lock_spec.platforms = (
        list(platform_overrides) if platform_overrides else lock_spec.platforms
    ) or list(DEFAULT_PLATFORMS)

    if required_categories is not None:

        def dep_has_category(d: Dependency, categories: AbstractSet[str]) -> bool:
            return d.category in categories

        lock_spec.dependencies = [
            d
            for d in lock_spec.dependencies
            if dep_has_category(d, categories=required_categories)
        ]

    return lock_spec


def make_lock_files(
    *,
    conda: PathLike,
    src_files: List[pathlib.Path],
    kinds: Sequence[TKindAll],
    lockfile_path: pathlib.Path = pathlib.Path(DEFAULT_LOCKFILE_NAME),
    platform_overrides: Optional[Sequence[str]] = None,
    channel_overrides: Optional[Sequence[str]] = None,
    virtual_package_spec: Optional[pathlib.Path] = None,
    update: Optional[List[str]] = None,
    include_dev_dependencies: bool = True,
    filename_template: Optional[str] = None,
    filter_categories: bool = True,
    extras: Optional[AbstractSet[str]] = None,
    check_input_hash: bool = False,
) -> None:
    """
    Generate a lock file from the src files provided

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    src_files :
        Files to parse requirements from
    kinds :
        Lockfile formats to output
    lockfile_path :
        Path to a conda-lock.yml to create or update
    platform_overrides :
        Platforms to solve for. Takes precedence over platforms found in src_files.
    channel_overrides :
        Channels to use. Takes precedence over channels found in src_files.
    virtual_package_spec :
        Path to a virtual package repository that defines each platform.
    update :
        Names of dependencies to update to their latest versions, regardless
        of whether the constraint in src_files has changed.
    include_dev_dependencies :
        Include development dependencies in explicit or env output
    filename_template :
        Format for names of rendered explicit or env files. Must include {platform}.
    extras :
        Include the given extras in explicit or env output
    filter_categories :
        Filter out unused categories prior to solving
    check_input_hash :
        Do not re-solve for each target platform for which specifications are unchanged
    """

    # initialize virtual package fake
    if virtual_package_spec and virtual_package_spec.exists():
        virtual_package_repo = virtual_package_repo_from_specification(
            virtual_package_spec
        )
    else:
        virtual_package_repo = default_virtual_package_repodata()

    required_categories = {"main"}
    if include_dev_dependencies:
        required_categories.add("dev")
    if extras is not None:
        required_categories.update(extras)

    with virtual_package_repo:
        lock_spec = make_lock_spec(
            src_files=src_files,
            channel_overrides=channel_overrides,
            platform_overrides=platform_overrides,
            virtual_package_repo=virtual_package_repo,
            required_categories=required_categories if filter_categories else None,
        )
        lock_content: Optional[Lockfile] = None

        platforms_to_lock: List[str] = []
        platforms_already_locked: List[str] = []
        if lockfile_path.exists():
            import yaml

            try:
                lock_content = parse_conda_lock_file(lockfile_path)
            except (yaml.error.YAMLError, FileNotFoundError):
                logger.warning(
                    "Failed to parse existing lock.  Regenerating from scratch"
                )
                lock_content = None
        else:
            lock_content = None

        if lock_content is not None:
            platforms_already_locked = list(lock_content.metadata.platforms)
            update_spec = UpdateSpecification(
                locked=lock_content.package, update=update
            )
            for platform in lock_spec.platforms:
                if (
                    update
                    or platform not in lock_content.metadata.platforms
                    or not check_input_hash
                    or lock_spec.content_hash_for_platform(platform)
                    != lock_content.metadata.content_hash[platform]
                ):
                    platforms_to_lock.append(platform)
                    if platform in platforms_already_locked:
                        platforms_already_locked.remove(platform)
        else:
            platforms_to_lock = lock_spec.platforms
            update_spec = UpdateSpecification()

        if platforms_already_locked:
            print(
                f"Spec hash already locked for {sorted(platforms_already_locked)}. Skipping solve.",
                file=sys.stderr,
            )
        platforms_to_lock = sorted(set(platforms_to_lock))

        if platforms_to_lock:
            print(f"Locking dependencies for {platforms_to_lock}...", file=sys.stderr)
            lock_content = lock_content | create_lockfile_from_spec(
                conda=conda,
                spec=lock_spec,
                platforms=platforms_to_lock,
                lockfile_path=lockfile_path,
                update_spec=update_spec,
            )

            if "lock" in kinds:
                write_conda_lock_file(lock_content, lockfile_path)
                print(
                    " - Install lock using:",
                    KIND_USE_TEXT["lock"].format(lockfile=str(lockfile_path)),
                    file=sys.stderr,
                )

        assert lock_content is not None

        do_render(
            lock_content,
            kinds=[k for k in kinds if k != "lock"],
            include_dev_dependencies=include_dev_dependencies,
            filename_template=filename_template,
            extras=extras,
            check_input_hash=check_input_hash,
        )


def do_render(
    lockfile: Lockfile,
    kinds: Sequence[Union[Literal["env"], Literal["explicit"]]],
    include_dev_dependencies: bool = True,
    filename_template: Optional[str] = None,
    extras: Optional[AbstractSet[str]] = None,
    check_input_hash: bool = False,
    override_platform: Optional[Sequence[str]] = None,
) -> None:
    """Render the lock content for each platform in lockfile

    Parameters
    ----------
    lockfile :
        Lock content
    kinds :
        Lockfile formats to render
    include_dev_dependencies :
        Include development dependencies in output
    filename_template :
        Format for the lock file names. Must include {platform}.
    extras :
        Include the given extras in output
    check_input_hash :
        Do not re-render if specifications are unchanged
    override_platform :
        Generate only this subset of the platform files
    """
    platforms = lockfile.metadata.platforms
    if override_platform is not None and len(override_platform) > 0:
        platforms = list(sorted(set(platforms) & set(override_platform)))

    if filename_template:
        if "{platform}" not in filename_template and len(platforms) > 1:
            print(
                "{platform} must be in filename template when locking"
                f" more than one platform: {', '.join(platforms)}",
                file=sys.stderr,
            )
            sys.exit(1)
        for kind, file_ext in KIND_FILE_EXT.items():
            if file_ext and filename_template.endswith(file_ext):
                print(
                    f"Filename template must not end with '{file_ext}', as this "
                    f"is reserved for '{kind}' lock files, in which case it is "
                    f"automatically added."
                )
                sys.exit(1)

    for plat in platforms:
        for kind in kinds:
            if filename_template:
                context = {
                    "platform": plat,
                    "dev-dependencies": str(include_dev_dependencies).lower(),
                    "input-hash": lockfile.metadata.content_hash,
                    "version": pkg_resources.get_distribution("conda_lock").version,
                    "timestamp": datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
                }

                filename = filename_template.format(**context)
            else:
                filename = f"conda-{plat}.lock"

            if pathlib.Path(filename).exists() and check_input_hash:
                with open(filename) as f:
                    previous_hash = extract_input_hash(f.read())
                if previous_hash == lockfile.metadata.content_hash.get(plat):
                    print(
                        f"Lock content already rendered for {plat}. Skipping render of {filename}.",
                        file=sys.stderr,
                    )
                    continue

            print(f"Rendering lockfile(s) for {plat}...", file=sys.stderr)
            lockfile_contents = render_lockfile_for_platform(
                lockfile=lockfile,
                include_dev_dependencies=include_dev_dependencies,
                extras=extras,
                kind=kind,
                platform=plat,
            )

            filename += KIND_FILE_EXT[kind]
            with open(filename, "w") as fo:
                fo.write("\n".join(lockfile_contents) + "\n")

            print(
                f" - Install lock using {'(see warning below)' if kind == 'env' else ''}:",
                KIND_USE_TEXT[kind].format(lockfile=filename),
                file=sys.stderr,
            )

    if "env" in kinds:
        print(
            "\nWARNING: Using environment lock files (*.yml) does NOT guarantee "
            "that generated environments will be identical over time, since the "
            "dependency resolver is re-run every time and changes in repository "
            "metadata or resolver logic may cause variation. Conversely, since "
            "the resolver is run every time, the resulting packages ARE "
            "guaranteed to be seen by conda as being in a consistent state. This "
            "makes them useful when updating existing environments.",
            file=sys.stderr,
        )


def render_lockfile_for_platform(  # noqa: C901
    *,
    lockfile: Lockfile,
    include_dev_dependencies: bool,
    extras: Optional[AbstractSet[str]],
    kind: Union[Literal["env"], Literal["explicit"]],
    platform: str,
) -> List[str]:
    """
    Render lock content into a single-platform lockfile that can be installed
    with conda.

    Parameters
    ----------
    lockfile :
        Locked package versions
    include_dev_dependencies :
        Include development dependencies in output
    extras :
        Optional dependency groups to include in output
    kind :
        Lockfile format (explicit or env)
    platform :
        Target platform

    """
    lockfile_contents = [
        "# Generated by conda-lock.",
        f"# platform: {platform}",
        f"# input_hash: {lockfile.metadata.content_hash.get(platform)}\n",
    ]

    categories = {
        "main",
        *(extras or []),
        *(["dev"] if include_dev_dependencies else []),
    }

    conda_deps: List[LockedDependency] = []
    pip_deps: List[LockedDependency] = []

    # ensure consistent ordering of generated file
    lockfile.toposort_inplace()

    for p in lockfile.package:
        if p.platform == platform and ((not p.optional) or (p.category in categories)):
            if p.manager == "pip":
                pip_deps.append(p)
            elif p.manager == "conda":
                # exclude virtual packages
                if not p.name.startswith("__"):
                    conda_deps.append(p)

    def format_pip_requirement(
        spec: LockedDependency, platform: str, direct: bool = False
    ) -> str:
        if spec.source and spec.source.type == "url":
            return f"{spec.name} @ {spec.source.url}"
        elif direct:
            s = f"{spec.name} @ {spec.url}"
            if spec.hash.sha256:
                s += f"#sha256={spec.hash.sha256}"
            return s
        else:
            s = f"{spec.name} === {spec.version}"
            if spec.hash.sha256:
                s += f" --hash=sha256:{spec.hash.sha256}"
            return s

    def format_conda_requirement(
        spec: LockedDependency, platform: str, direct: bool = False
    ) -> str:
        if direct:
            # inject the environment variables in here
            return posixpath.expandvars(f"{spec.url}#{spec.hash.md5}")
        else:
            path = pathlib.Path(urlsplit(spec.url).path)
            while path.suffix in {".tar", ".bz2", ".gz", ".conda"}:
                path = path.with_suffix("")
            build_string = path.name.split("-")[-1]
            return f"{spec.name}={spec.version}={build_string}"

    if kind == "env":
        lockfile_contents.extend(
            [
                "channels:",
                *(
                    f"  - {channel.env_replaced_url()}"
                    for channel in lockfile.metadata.channels
                ),
                "dependencies:",
                *(
                    f"  - {format_conda_requirement(dep, platform, direct=False)}"
                    for dep in conda_deps
                ),
            ]
        )
        lockfile_contents.extend(
            [
                "  - pip:",
                *(
                    f"    - {format_pip_requirement(dep, platform, direct=False)}"
                    for dep in pip_deps
                ),
            ]
            if pip_deps
            else []
        )
    elif kind == "explicit":
        lockfile_contents.append("@EXPLICIT\n")

        lockfile_contents.extend(
            [format_conda_requirement(dep, platform, direct=True) for dep in conda_deps]
        )

        def sanitize_lockfile_line(line: str) -> str:
            line = line.strip()
            if line == "":
                return "#"
            else:
                return line

        lockfile_contents = [sanitize_lockfile_line(line) for line in lockfile_contents]

        # emit an explicit requirements.txt, prefixed with '# pip '
        lockfile_contents.extend(
            [
                f"# pip {format_pip_requirement(dep, platform, direct=True)}"
                for dep in pip_deps
            ]
        )
    else:
        raise ValueError(f"Unrecognised lock kind {kind}.")

    logging.debug("lockfile_contents:\n%s\n", lockfile_contents)
    return lockfile_contents


def _solve_for_arch(
    conda: PathLike,
    spec: LockSpecification,
    platform: str,
    channels: List[Channel],
    update_spec: Optional[UpdateSpecification] = None,
) -> List[LockedDependency]:
    """
    Solve specification for a single platform
    """
    if update_spec is None:
        update_spec = UpdateSpecification()
    # filter requested and locked dependencies to the current platform
    dependencies = [
        dep
        for dep in spec.dependencies
        if (not dep.selectors.platform) or platform in dep.selectors.platform
    ]
    locked = [dep for dep in update_spec.locked if dep.platform == platform]
    requested_deps_by_name = {
        manager: {dep.name: dep for dep in dependencies if dep.manager == manager}
        for manager in ("conda", "pip")
    }
    locked_deps_by_name = {
        manager: {dep.name: dep for dep in locked if dep.manager == manager}
        for manager in ("conda", "pip")
    }

    conda_deps = solve_conda(
        conda,
        specs=requested_deps_by_name["conda"],
        locked=locked_deps_by_name["conda"],
        update=update_spec.update,
        platform=platform,
        channels=channels,
    )

    if requested_deps_by_name["pip"]:
        if not PIP_SUPPORT:
            raise ValueError("pip support is not enabled")
        if "python" not in conda_deps:
            raise ValueError("Got pip specs without Python")
        pip_deps = solve_pypi(
            requested_deps_by_name["pip"],
            use_latest=update_spec.update,
            pip_locked={
                dep.name: dep for dep in update_spec.locked if dep.manager == "pip"
            },
            conda_locked={dep.name: dep for dep in conda_deps.values()},
            python_version=conda_deps["python"].version,
            platform=platform,
        )
    else:
        pip_deps = {}

    return list(conda_deps.values()) + list(pip_deps.values())


def create_lockfile_from_spec(
    *,
    conda: PathLike,
    spec: LockSpecification,
    platforms: List[str] = [],
    lockfile_path: pathlib.Path,
    update_spec: Optional[UpdateSpecification] = None,
) -> Lockfile:
    """
    Solve or update specification
    """
    assert spec.virtual_package_repo is not None
    virtual_package_channel = spec.virtual_package_repo.channel

    locked: Dict[Tuple[str, str, str], LockedDependency] = {}

    for platform in platforms or spec.platforms:

        deps = _solve_for_arch(
            conda=conda,
            spec=spec,
            platform=platform,
            channels=[*spec.channels, virtual_package_channel],
            update_spec=update_spec,
        )

        for dep in deps:
            locked[(dep.manager, dep.name, dep.platform)] = dep

    return Lockfile(
        package=[locked[k] for k in locked],
        metadata=LockMeta(
            content_hash=spec.content_hash(),
            channels=[c for c in spec.channels],
            platforms=spec.platforms,
            sources=[str(source.resolve()) for source in spec.sources],
        ),
    )


def parse_source_files(
    src_files: List[pathlib.Path],
    platform_overrides: Sequence[str],
) -> List[LockSpecification]:
    """
    Parse a sequence of dependency specifications from source files

    Parameters
    ----------
    src_files :
        Files to parse for dependencies
    platform_overrides :
        Target platforms to render meta.yaml files for
    """
    desired_envs: List[LockSpecification] = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(
                parse_meta_yaml_file(src_file, list(platform_overrides))
            )
        elif src_file.name == "pyproject.toml":
            desired_envs.append(parse_pyproject_toml(src_file))
        else:
            desired_envs.append(
                parse_environment_file(src_file, pip_support=PIP_SUPPORT)
            )
    return desired_envs


def _add_auth_to_line(line: str, auth: Dict[str, str]) -> str:
    matching_auths = [a for a in auth if a in line]
    if not matching_auths:
        return line
    # If we have multiple matching auths, we choose the longest one.
    matching_auth = max(matching_auths, key=len)
    replacement = f"{auth[matching_auth]}@{matching_auth}"
    return line.replace(matching_auth, replacement)


def _add_auth_to_lockfile(lockfile: str, auth: Dict[str, str]) -> str:
    lockfile_with_auth = "\n".join(
        _add_auth_to_line(line, auth) if line[0] not in ("#", "@") else line
        for line in lockfile.strip().split("\n")
    )
    if lockfile.endswith("\n"):
        return lockfile_with_auth + "\n"
    return lockfile_with_auth


@contextmanager
def _add_auth(lockfile: str, auth: Dict[str, str]) -> Iterator[pathlib.Path]:
    lockfile_with_auth = _add_auth_to_lockfile(lockfile, auth)
    with temporary_file_with_contents(lockfile_with_auth) as path:
        yield path


def _strip_auth_from_line(line: str) -> str:
    return AUTH_PATTERN.sub(r"\1\3", line)


def _extract_domain(line: str) -> str:
    return DOMAIN_PATTERN.sub(r"\2", line)


def _strip_auth_from_lockfile(lockfile: str) -> str:
    lockfile_lines = lockfile.strip().split("\n")
    stripped_lockfile_lines = tuple(
        _strip_auth_from_line(line) if line[0] not in ("#", "@") else line
        for line in lockfile_lines
    )
    stripped_domains = sorted(
        {
            _extract_domain(stripped_line)
            for line, stripped_line in zip(lockfile_lines, stripped_lockfile_lines)
            if line != stripped_line
        }
    )
    stripped_lockfile = "\n".join(stripped_lockfile_lines)
    if lockfile.endswith("\n"):
        stripped_lockfile += "\n"
    if stripped_domains:
        stripped_domains_doc = "\n".join(f"# - {domain}" for domain in stripped_domains)
        return f"# The following domains require authentication:\n{stripped_domains_doc}\n{stripped_lockfile}"
    return stripped_lockfile


@contextmanager
def _render_lockfile_for_install(
    filename: pathlib.Path,
    include_dev_dependencies: bool = True,
    extras: Optional[AbstractSet[str]] = None,
) -> Iterator[pathlib.Path]:
    """
    Render lock content into a temporary, explicit lockfile for the current platform

    Parameters
    ----------
    filename :
        Path to conda-lock.yml
    include_dev_dependencies :
        Include development dependencies in output
    extras :
        Optional dependency groups to include in output

    """

    if not filename.name.endswith(DEFAULT_LOCKFILE_NAME):
        yield filename
        return

    from ensureconda.resolve import platform_subdir

    lock_content = parse_conda_lock_file(pathlib.Path(filename))

    platform = platform_subdir()
    if platform not in lock_content.metadata.platforms:
        raise PlatformValidationError(
            f"Dependencies are not locked for the current platform ({platform})"
        )

    # TODO: Move to LockFile
    required_env_vars: Set[str] = set()
    for channel in lock_content.metadata.channels:
        required_env_vars.update(channel.used_env_vars)
    existing_env_vars = {k for k, v in os.environ.items() if v}
    missing_env_vars = required_env_vars - existing_env_vars
    if missing_env_vars:
        msg = ", ".join(sorted(missing_env_vars))
        raise MissingEnvVarError(
            f"Cannot run render lockfile.  Missing environment variables: {msg}"
        )

    content = render_lockfile_for_platform(
        lockfile=lock_content,
        kind="explicit",
        platform=platform,
        include_dev_dependencies=include_dev_dependencies,
        extras=extras,
    )
    with temporary_file_with_contents("\n".join(content) + "\n") as path:
        yield path


def run_lock(
    environment_files: List[pathlib.Path],
    *,
    conda_exe: Optional[str],
    platforms: Optional[List[str]] = None,
    mamba: bool = False,
    micromamba: bool = False,
    include_dev_dependencies: bool = True,
    channel_overrides: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
    kinds: Optional[Sequence[TKindAll]] = None,
    lockfile_path: pathlib.Path = pathlib.Path(DEFAULT_LOCKFILE_NAME),
    check_input_hash: bool = False,
    extras: Optional[AbstractSet[str]] = None,
    virtual_package_spec: Optional[pathlib.Path] = None,
    update: Optional[List[str]] = None,
    filter_categories: bool = False,
) -> None:
    if environment_files == DEFAULT_FILES:
        if lockfile_path.exists():
            lock_content = parse_conda_lock_file(lockfile_path)
            # reconstruct native paths
            locked_environment_files = [
                pathlib.Path(
                    pathlib.PurePosixPath(lockfile_path).parent
                    / pathlib.PurePosixPath(p)
                )
                for p in lock_content.metadata.sources
            ]
            if all(p.exists() for p in locked_environment_files):
                environment_files = locked_environment_files
            else:
                missing = [p for p in locked_environment_files if not p.exists()]
                print(
                    f"{lockfile_path} was created from {[str(p) for p in locked_environment_files]},"
                    f" but some files ({[str(p) for p in missing]}) do not exist. Falling back to"
                    f" {[str(p) for p in environment_files]}.",
                    file=sys.stderr,
                )
        else:
            long_ext_file = pathlib.Path("environment.yaml")
            if long_ext_file.exists() and not environment_files[0].exists():
                environment_files = [long_ext_file]

    _conda_exe = determine_conda_executable(
        conda_exe, mamba=mamba, micromamba=micromamba
    )
    make_lock_files(
        conda=_conda_exe,
        src_files=environment_files,
        platform_overrides=platforms,
        channel_overrides=channel_overrides,
        virtual_package_spec=virtual_package_spec,
        update=update,
        kinds=kinds or DEFAULT_KINDS,
        lockfile_path=lockfile_path,
        filename_template=filename_template,
        include_dev_dependencies=include_dev_dependencies,
        extras=extras,
        check_input_hash=check_input_hash,
        filter_categories=filter_categories,
    )


@click.group(cls=OrderedGroup, default="lock", default_if_no_args=True)
@click.version_option()
def main() -> None:
    """To get help for subcommands, use the conda-lock <SUBCOMMAND> --help"""
    pass


TLogLevel = Union[
    Literal["DEBUG"],
    Literal["INFO"],
    Literal["WARNING"],
    Literal["ERROR"],
    Literal["CRITICAL"],
]


@main.command("lock")
@click.option(
    "--conda", default=None, help="path (or name) of the conda/mamba executable to use."
)
@click.option(
    "--mamba/--no-mamba",
    default=HAVE_MAMBA,
    help="don't attempt to use or install mamba.",
)
@click.option(
    "--micromamba/--no-micromamba",
    default=False,
    help="don't attempt to use or install micromamba.",
)
@click.option(
    "-p",
    "--platform",
    multiple=True,
    help="generate lock files for the following platforms",
)
@click.option(
    "-c",
    "--channel",
    "channel_overrides",
    multiple=True,
    help="""Override the channels to use when solving the environment. These will replace the channels as listed in the various source files.""",
)
@click.option(
    "--dev-dependencies/--no-dev-dependencies",
    is_flag=True,
    default=True,
    help="include dev dependencies in the lockfile (where applicable)",
)
@click.option(
    "-f",
    "--file",
    "files",
    default=DEFAULT_FILES,
    type=click.Path(),
    multiple=True,
    help="path to a conda environment specification(s)",
)
@click.option(
    "-k",
    "--kind",
    default=["lock"],
    type=str,
    multiple=True,
    help="Kind of lock file(s) to generate [should be one of 'lock', 'explicit', or 'env'].",
)
@click.option(
    "--filename-template",
    default="conda-{platform}.lock",
    help="Template for single-platform (explicit, env) lock file names. Filename must include {platform} token, and must not end in '.yml'. For a full list and description of available tokens, see the command help text.",
)
@click.option(
    "--lockfile",
    default=DEFAULT_LOCKFILE_NAME,
    help="Path to a conda-lock.yml to create or update",
)
@click.option(
    "--strip-auth",
    is_flag=True,
    default=False,
    help="Strip the basic auth credentials from the lockfile.",
)
@click.option(
    "-e",
    "--extras",
    "--category",
    default=[],
    type=str,
    multiple=True,
    help="When used in conjunction with input sources that support extras/categories (pyproject.toml) will add the deps from those extras to the render specification",
)
@click.option(
    "--filter-categories",
    "--filter-extras",
    is_flag=True,
    default=False,
    help="In conjunction with extras this will prune out dependencies that do not have the extras specified when loading files.",
)
@click.option(
    "--check-input-hash",
    is_flag=True,
    default=False,
    help="Check existing input hashes in lockfiles before regenerating lock files.  If no files were updated exit with exit code 4.  Incompatible with --strip-auth",
)
@click.option(
    "--log-level",
    help="Log level.",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
)
@click.option(
    "--pdb", is_flag=True, help="Drop into a postmortem debugger if conda-lock crashes"
)
@click.option(
    "--virtual-package-spec",
    type=click.Path(),
    help="Specify a set of virtual packages to use.",
)
@click.option(
    "--update",
    multiple=True,
    help="Packages to update to their latest versions. If empty, update all.",
)
@click.option(
    "--pypi_to_conda_lookup_file",
    type=str,
    help="Location of the lookup file containing Pypi package names to conda names.",
)
@click.pass_context
def lock(
    ctx: click.Context,
    conda: Optional[PathLike],
    mamba: bool,
    micromamba: bool,
    platform: List[str],
    channel_overrides: List[str],
    dev_dependencies: bool,
    files: List[pathlib.Path],
    kind: List[Union[Literal["lock"], Literal["env"], Literal["explicit"]]],
    filename_template: str,
    lockfile: PathLike,
    strip_auth: bool,
    extras: List[str],
    filter_categories: bool,
    check_input_hash: bool,
    log_level: TLogLevel,
    pdb: bool,
    virtual_package_spec: Optional[PathLike],
    pypi_to_conda_lookup_file: Optional[str],
    update: Optional[List[str]] = None,
) -> None:
    """Generate fully reproducible lock files for conda environments.

    By default, the lock files are written to conda-{platform}.lock. These filenames can be customized using the
    --filename-template argument. The following tokens are available:

    \b
        platform: The platform this lock file was generated for (conda subdir).
        dev-dependencies: Whether or not dev dependencies are included in this lock file.
        input-hash: A sha256 hash of the lock file input specification.
        version: The version of conda-lock used to generate this lock file.
        timestamp: The approximate timestamp of the output file in ISO8601 basic format.
    """
    logging.basicConfig(level=log_level)

    # Set Pypi <--> Conda lookup file location
    if pypi_to_conda_lookup_file:
        set_lookup_location(pypi_to_conda_lookup_file)

    # bail out if we do not encounter the default file if no files were passed
    if ctx.get_parameter_source("files") == click.core.ParameterSource.DEFAULT:
        candidates = list(files)
        candidates += [f.with_name(f.name.replace(".yml", ".yaml")) for f in candidates]
        for f in candidates:
            if f.exists():
                break
        else:
            print(ctx.get_help())
            sys.exit(1)

    if pdb:
        sys.excepthook = _handle_exception_post_mortem

    if not virtual_package_spec:
        candidates = [
            pathlib.Path("virtual-packages.yml"),
            pathlib.Path("virtual-packages.yaml"),
        ]
        for c in candidates:
            if c.exists():
                logger.info("Using virtual packages from %s", c)
                virtual_package_spec = c
                break
    else:
        virtual_package_spec = pathlib.Path(virtual_package_spec)

    files = [pathlib.Path(file) for file in files]
    extras_ = set(extras)
    lock_func = partial(
        run_lock,
        environment_files=files,
        conda_exe=conda,
        platforms=platform,
        mamba=mamba,
        micromamba=micromamba,
        include_dev_dependencies=dev_dependencies,
        channel_overrides=channel_overrides,
        kinds=kind,
        lockfile_path=pathlib.Path(lockfile),
        extras=extras_,
        virtual_package_spec=virtual_package_spec,
        update=update,
        filter_categories=filter_categories,
    )
    if strip_auth:
        with tempfile.TemporaryDirectory() as tempdir:
            filename_template_temp = f"{tempdir}/{filename_template.split('/')[-1]}"
            lock_func(filename_template=filename_template_temp)
            filename_template_dir = "/".join(filename_template.split("/")[:-1])
            for file in os.listdir(tempdir):
                lockfile = read_file(os.path.join(tempdir, file))
                lockfile = _strip_auth_from_lockfile(lockfile)
                write_file(lockfile, os.path.join(filename_template_dir, file))
    else:
        lock_func(
            filename_template=filename_template, check_input_hash=check_input_hash
        )


@main.command("install")
@click.option(
    "--conda", default=None, help="path (or name) of the conda/mamba executable to use."
)
@click.option(
    "--mamba/--no-mamba",
    default=HAVE_MAMBA,
    help="don't attempt to use or install mamba.",
)
@click.option(
    "--micromamba/--no-micromamba",
    default=False,
    help="don't attempt to use or install micromamba.",
)
@click.option("-p", "--prefix", help="Full path to environment location (i.e. prefix).")
@click.option("-n", "--name", help="Name of environment.")
@click.option(
    "--auth",
    help="The auth file provided as string. Has precedence over `--auth-file`.",
    default="",
)
@click.option("--auth-file", help="Path to the authentication file.", default="")
@click.option(
    "--validate-platform/--no-validate-platform",
    default=True,
    help="Whether the platform compatibility between your lockfile and the host system should be validated.",
)
@click.option(
    "--log-level",
    help="Log level.",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
)
@click.option(
    "--dev/--no-dev",
    is_flag=True,
    default=True,
    help="install dev dependencies from the lockfile (where applicable)",
)
@click.option(
    "-E",
    "--extras",
    multiple=True,
    default=[],
    help="include extra dependencies from the lockfile (where applicable)",
)
@click.argument(
    "lock-file", default=pathlib.Path(DEFAULT_LOCKFILE_NAME), type=click.Path()
)
@click.pass_context
def install(
    ctx: click.Context,
    conda: Optional[str],
    mamba: bool,
    micromamba: bool,
    prefix: Optional[str],
    name: Optional[str],
    lock_file: pathlib.Path,
    auth: Optional[str],
    auth_file: Optional[PathLike],
    validate_platform: bool,
    log_level: TLogLevel,
    dev: bool,
    extras: List[str],
) -> None:
    # bail out if we do not encounter the lockfile
    lock_file = pathlib.Path(lock_file)
    if not lock_file.exists():
        print(ctx.get_help())
        sys.exit(1)

    """Perform a conda install"""
    logging.basicConfig(level=log_level)
    _auth = (
        yaml.safe_load(auth) if auth else read_json(auth_file) if auth_file else None
    )
    _conda_exe = determine_conda_executable(conda, mamba=mamba, micromamba=micromamba)
    install_func = partial(do_conda_install, conda=_conda_exe, prefix=prefix, name=name)
    if validate_platform and not lock_file.name.endswith(DEFAULT_LOCKFILE_NAME):
        lockfile_contents = read_file(lock_file)
        try:
            do_validate_platform(lockfile_contents)
        except PlatformValidationError as error:
            raise PlatformValidationError(
                error.args[0] + " Disable validation with `--no-validate-platform`."
            )
    with _render_lockfile_for_install(
        lock_file, include_dev_dependencies=dev, extras=set(extras)
    ) as lockfile:
        if auth:
            with _add_auth(read_file(lockfile), _auth) as lockfile_with_auth:
                install_func(file=lockfile_with_auth)
        else:
            install_func(file=lockfile)


@main.command("render")
@click.option(
    "--dev-dependencies/--no-dev-dependencies",
    is_flag=True,
    default=True,
    help="include dev dependencies in the lockfile (where applicable)",
)
@click.option(
    "-k",
    "--kind",
    default=["explicit"],
    type=click.Choice(["explicit", "env"]),
    multiple=True,
    help="Kind of lock file(s) to generate.",
)
@click.option(
    "--filename-template",
    default="conda-{platform}.lock",
    help="Template for the lock file names. Filename must include {platform} token, and must not end in '.yml'. For a full list and description of available tokens, see the command help text.",
)
@click.option(
    "-e",
    "--extras",
    default=[],
    type=str,
    multiple=True,
    help="When used in conjunction with input sources that support extras (pyproject.toml) will add the deps from those extras to the input specification",
)
@click.option(
    "--log-level",
    help="Log level.",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
)
@click.option(
    "--pdb", is_flag=True, help="Drop into a postmortem debugger if conda-lock crashes"
)
@click.option(
    "-p",
    "--platform",
    multiple=True,
    help="render lock files for the following platforms",
)
@click.argument("lock-file", default=DEFAULT_LOCKFILE_NAME)
@click.pass_context
def render(
    ctx: click.Context,
    dev_dependencies: bool,
    kind: Sequence[Union[Literal["env"], Literal["explicit"]]],
    filename_template: str,
    extras: List[str],
    log_level: TLogLevel,
    lock_file: PathLike,
    pdb: bool,
    platform: Sequence[str],
) -> None:
    """Render multi-platform lockfile into single-platform env or explicit file"""
    logging.basicConfig(level=log_level)

    if pdb:
        sys.excepthook = _handle_exception_post_mortem

    # bail out if we do not encounter the lockfile
    lock_file = pathlib.Path(lock_file)
    if not lock_file.exists():
        print(ctx.get_help())
        sys.exit(1)

    lock_content = parse_conda_lock_file(lock_file)

    do_render(
        lock_content,
        filename_template=filename_template,
        kinds=kind,
        include_dev_dependencies=dev_dependencies,
        extras=set(extras),
        override_platform=platform,
    )


def _handle_exception_post_mortem(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_traceback: Optional[TracebackType],
) -> Any:
    import pdb

    pdb.post_mortem(exc_traceback)


if __name__ == "__main__":
    main()
