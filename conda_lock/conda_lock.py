"""
Somewhat hacky solution to create conda lock files.
"""

import datetime
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from contextlib import contextmanager
from functools import partial
from itertools import chain
from typing import AbstractSet, Dict, Iterator, List, Optional, Sequence, Tuple, cast
from urllib.parse import urlsplit

import click
import pkg_resources
import toml

from click_default_group import DefaultGroup

from conda_lock.common import read_file, read_json, write_file
from conda_lock.conda_solver import solve_conda
from conda_lock.errors import PlatformValidationError
from conda_lock.invoke_conda import PathLike, _invoke_conda, determine_conda_executable


try:
    from conda_lock.pypi_solver import solve_pypi

    pip_support = True
except ImportError:
    pip_support = False
from conda_lock.src_parser import (
    LockedDependency,
    Lockfile,
    LockMeta,
    LockSpecification,
    UpdateSpecification,
    aggregate_lock_specs,
)
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.lockfile import parse_conda_lock_file
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


if not (sys.version_info.major >= 3 and sys.version_info.minor >= 6):
    print("conda_lock needs to run under python >=3.6")
    sys.exit(1)


CONDA_PKGS_DIRS = None
DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]
DEFAULT_KINDS = ["explicit"]
KIND_FILE_EXT = {
    "explicit": "",
    "env": ".yml",
    "lock": "",
}
KIND_USE_TEXT = {
    "explicit": "conda create --name YOURENV --file {lockfile}",
    "env": "conda env create --name YOURENV --file {lockfile}",
    "lock": "...you're on your own, buddy",
}

LOCKFILE_NAME = "conda-lock.toml"


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
    return platform == determined_subdir, platform


def do_validate_platform(lockfile: str):
    platform_lockfile = extract_platform(lockfile)
    try:
        success, platform_sys = _do_validate_platform(platform_lockfile)
    except KeyError:
        raise RuntimeError(f"Unknown platform type in lockfile '{platform_lockfile}'.")
    if not success:
        raise PlatformValidationError(
            f"Platform in lockfile '{platform_lockfile}' is not compatible with system platform '{platform_sys}'."
        )


def do_conda_install(conda: PathLike, prefix: str, name: str, file: str) -> None:

    _conda = partial(_invoke_conda, conda, prefix, name)

    kind = "env" if file.endswith(".yml") else "explicit"

    if kind == "explicit":
        with open(file) as explicit_env:
            pip_requirements = [
                line.split("# pip ")[1]
                for line in explicit_env
                if line.startswith("# pip ")
            ]
    else:
        pip_requirements = []

    if (
        _conda(
            [
                *(["env"] if kind == "env" else []),
                "create",
                "--file",
                file,
                *([] if kind == "env" else ["--yes"]),
            ],
        ).returncode
        != 0
    ):
        print(
            f"Could not perform conda install using {file} lock file into {name or prefix}"
        )
        sys.exit(1)

    if not pip_requirements:
        return

    with tempfile.NamedTemporaryFile() as tf:
        write_file("\n".join(pip_requirements), tf.name)
        if (
            _conda(["run"], ["pip", "install", "--no-deps", "-r", tf.name])
        ).returncode != 0:
            print(
                f"Could not perform pip install using {file} lock file into {name or prefix}"
            )
            sys.exit(1)


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
) -> LockSpecification:
    """Generate the lockfile specs from a set of input src_files"""
    lock_specs = parse_source_files(
        src_files=src_files, platform_overrides=platform_overrides or DEFAULT_PLATFORMS
    )

    lock_spec = aggregate_lock_specs(lock_specs)
    lock_spec.virtual_package_repo = virtual_package_repo
    lock_spec.channels = (
        list(channel_overrides) if channel_overrides else lock_spec.channels
    )
    lock_spec.platforms = (
        list(platform_overrides) if platform_overrides else lock_spec.platforms
    )

    return lock_spec


def make_lock_files(
    conda: PathLike,
    src_files: List[pathlib.Path],
    kinds: List[str],
    platform_overrides: Optional[Sequence[str]] = None,
    channel_overrides: Optional[Sequence[str]] = None,
    virtual_package_spec: Optional[pathlib.Path] = None,
    update: Optional[List[str]] = None,
    include_dev_dependencies: bool = True,
    filename_template: Optional[str] = None,
    extras: Optional[AbstractSet[str]] = None,
    check_input_hash: bool = False,
):
    """Generate a lock file from the src file provided"""

    # initialize virtual package fake
    if virtual_package_spec and virtual_package_spec.exists():
        virtual_package_repo = virtual_package_repo_from_specification(
            virtual_package_spec
        )
    else:
        virtual_package_repo = default_virtual_package_repodata()

    with virtual_package_repo:
        lock_spec = make_lock_spec(
            src_files=src_files,
            channel_overrides=channel_overrides,
            platform_overrides=platform_overrides,
            virtual_package_repo=virtual_package_repo,
        )

        kind = "lock"
        filename = LOCKFILE_NAME

        lockfile = pathlib.Path(filename)

        lock_content: Optional[Lockfile] = None
        if lockfile.exists():
            lock_content = parse_conda_lock_file(lockfile)
            update_spec = UpdateSpecification(
                locked=lock_content.package, update=update
            )
            if not (
                check_input_hash
                and lock_content.metadata.content_hash == lock_spec.content_hash()
            ):
                lock_content = None
        else:
            update_spec = UpdateSpecification()

        if lock_content is None:
            print(f"Locking dependencies for {lock_spec.platforms}...", file=sys.stderr)
            lock_content = create_lockfile_from_spec(
                conda=conda,
                spec=lock_spec,
                update_spec=update_spec,
            )

            if "lock" in kinds:
                with open(filename, "w") as f:
                    toml.dump(lock_content.dict(by_alias=True, exclude_unset=True), f)
                print(
                    f" - Install lock using {'(see warning below)' if kind == 'env' else ''}:",
                    KIND_USE_TEXT[kind].format(lockfile=filename),
                    file=sys.stderr,
                )
        else:
            print(
                f"Spec hash already locked for {lock_spec.platforms}. Skipping",
                file=sys.stderr,
            )

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
    kinds: List[str],
    include_dev_dependencies: bool = True,
    filename_template: Optional[str] = None,
    extras: Optional[AbstractSet[str]] = None,
    check_input_hash: bool = False,
):
    """Render the lock content for each platform

    Parameters
    ----------
    include_dev_dependencies :
        Include development dependencies in output
    filename_template :
        Format for the lock file names. Must include {platform}.
    extras :
        Include the given extras in output

    """

    platforms = set(
        chain.from_iterable(package.platforms for package in lockfile.package)
    )

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
                if previous_hash == lockfile.metadata.content_hash:
                    print(
                        f"Spec hash already locked for {plat}. Skipping",
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
    kind: str,
    platform: str,
) -> List[str]:

    lockfile_contents = [
        "# Generated by conda-lock.",
        f"# platform: {platform}",
        f"# input_hash: {lockfile.metadata.content_hash}\n",
    ]

    categories = {
        "main",
        *(extras or []),
        *(["dev"] if include_dev_dependencies else []),
    }

    conda_deps = []
    pip_deps = []
    for p in lockfile.package:

        if platform in p.platforms and ((not p.optional) or (p.category in categories)):
            if p.manager == "pip":
                pip_deps.append(p)
            # exclude virtual packages
            elif not p.name.startswith("__"):
                conda_deps.append(p)

    def format_pip_requirement(
        spec: LockedDependency, platform: str, direct=False
    ) -> str:
        if spec.source and spec.source.type == "url":
            return f"{spec.name} @ {spec.source.url}"
        elif direct:
            pkg = spec.packages[platform]
            return f'{spec.name} @ {pkg.url}#{pkg.hash.replace(":", "=")}'
        else:
            return f"{spec.name} === {spec.version}"

    def format_conda_requirement(
        spec: LockedDependency, platform: str, direct=False
    ) -> str:
        pkg = spec.packages[platform]
        if direct:
            return f"{pkg.url}#{pkg.hash}"
        else:
            path = pathlib.Path(urlsplit(pkg.url).path)
            while path.suffix in {".tar", ".bz2", ".gz", ".conda"}:
                path = path.with_suffix("")
            build_string = path.name.split("-")[-1]
            return f"{spec.name}={spec.version}={build_string}"

    if kind == "env":
        lockfile_contents.extend(
            [
                "channels:",
                *(f"  - {channel}" for channel in lockfile.metadata.channels),
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
        )
    elif kind == "explicit":
        lockfile_contents.append("@EXPLICIT\n")

        lockfile_contents.extend(
            sorted(
                [
                    format_conda_requirement(dep, platform, direct=True)
                    for dep in conda_deps
                ]
            )
        )

        def sanitize_lockfile_line(line):
            line = line.strip()
            if line == "":
                return "#"
            else:
                return line

        lockfile_contents = [sanitize_lockfile_line(line) for line in lockfile_contents]

        # emit an explicit requirements.txt, prefixed with '# pip '
        lockfile_contents.extend(
            sorted(
                [
                    f"# pip {format_pip_requirement(dep, platform, direct=True)}"
                    for dep in pip_deps
                ]
            )
        )
    else:
        raise ValueError(f"Unrecognised lock kind {kind}.")

    logging.debug("lockfile_contents:\n%s\n", lockfile_contents)
    return lockfile_contents


def _solve_for_arch(
    conda: PathLike,
    spec: LockSpecification,
    platform: str,
    channels: List[str],
    update_spec: UpdateSpecification = UpdateSpecification(),
) -> List[LockedDependency]:
    # filter requested and locked dependencies to the current platform
    dependencies = [
        dep
        for dep in spec.dependencies
        if (not dep.selectors.platform) or platform in dep.selectors.platform
    ]
    locked = [dep for dep in update_spec.locked if platform in dep.platforms]
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
        if not pip_support:
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
    update_spec: UpdateSpecification = UpdateSpecification(),
) -> Lockfile:
    assert spec.virtual_package_repo is not None
    virtual_package_channel = spec.virtual_package_repo.channel_url

    locked: Dict[Tuple[str, str], LockedDependency] = {}

    spec.content_hash()

    for platform in spec.platforms:

        deps = _solve_for_arch(
            conda=conda,
            spec=spec,
            platform=platform,
            channels=[*spec.channels, virtual_package_channel],
            update_spec=update_spec,
        )

        for dep in deps:
            key = dep.manager, dep.name
            if key in locked:
                target = locked[key]
                if target.version != dep.version:
                    raise ValueError(
                        f"Platform inconsistency ({dep.manager}): {dep.name} resolves to {dep.version} on {platform}, but {target.version} on {target.platforms[-1]}"
                    )
                target.platforms.append(platform)
                target.packages.update(dep.packages)
            else:
                locked[key] = dep

    return Lockfile(
        package=[locked[k] for k in sorted(locked.keys())],
        metadata=LockMeta(
            content_hash=spec.content_hash(),
            channels=spec.channels,
            platforms=spec.platforms,
        ),
    )


def main_on_docker(env_file, platforms):
    env_path = pathlib.Path(env_file)
    platform_arg = []
    for p in platforms:
        platform_arg.extend(["--platform", p])

    subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{str(env_path.parent)}:/work:rwZ",
            "--workdir",
            "/work",
            "conda-lock:latest",
            "--file",
            env_path.name,
            *platform_arg,
        ]
    )


def parse_source_files(
    src_files: List[pathlib.Path],
    platform_overrides: Sequence[str],
) -> List[LockSpecification]:
    desired_envs = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(
                parse_meta_yaml_file(src_file, list(platform_overrides))
            )
        elif src_file.name == "pyproject.toml":
            desired_envs.append(parse_pyproject_toml(src_file))
        else:
            desired_envs.append(parse_environment_file(src_file, pip_support))
    return desired_envs


def _add_auth_to_line(line: str, auth: Dict[str, str]):
    search = DOMAIN_PATTERN.search(line)
    if search and search.group(2) in auth:
        return f"{search.group(1)}{auth[search.group(2)]}@{search.group(2)}{search.group(3)}"
    return line


def _add_auth_to_lockfile(lockfile: str, auth: Dict[str, str]) -> str:
    lockfile_with_auth = "\n".join(
        _add_auth_to_line(line, auth) if line[0] not in ("#", "@") else line
        for line in lockfile.strip().split("\n")
    )
    if lockfile.endswith("\n"):
        return lockfile_with_auth + "\n"
    return lockfile_with_auth


@contextmanager
def _add_auth(lockfile: str, auth: Dict[str, str]) -> Iterator[str]:
    with tempfile.NamedTemporaryFile() as tf:
        lockfile_with_auth = _add_auth_to_lockfile(lockfile, auth)
        write_file(lockfile_with_auth, tf.name)
        yield tf.name


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
    filename: str,
    include_dev_dependencies: bool = True,
    extras: Optional[AbstractSet[str]] = None,
):

    if not filename.endswith(".toml"):
        yield filename
        return

    from ensureconda.resolve import platform_subdir

    with open(filename) as f:
        lockfile = cast(Lockfile, toml.load(f))

    # platform = platform_subdir()
    platform = "linux-64"
    if platform not in lockfile.metadata.platforms:
        raise PlatformValidationError(
            f"Dependencies are not locked for the current platform ({platform})"
        )

    with tempfile.NamedTemporaryFile(mode="w") as tf:
        content = render_lockfile_for_platform(
            lockfile=lockfile,
            kind="explicit",
            platform=platform,
            include_dev_dependencies=include_dev_dependencies,
            extras=extras,
        )
        tf.write("\n".join(content) + "\n")
        tf.flush()
        yield tf.name


def run_lock(
    environment_files: List[pathlib.Path],
    conda_exe: Optional[str],
    platforms: Optional[List[str]] = None,
    mamba: bool = False,
    micromamba: bool = False,
    include_dev_dependencies: bool = True,
    channel_overrides: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
    kinds: Optional[List[str]] = None,
    check_input_hash: bool = False,
    extras: Optional[AbstractSet[str]] = None,
    virtual_package_spec: Optional[pathlib.Path] = None,
    update: Optional[List[str]] = None,
) -> None:
    if environment_files == DEFAULT_FILES:
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
        filename_template=filename_template,
        include_dev_dependencies=include_dev_dependencies,
        extras=extras,
        check_input_hash=check_input_hash,
    )


@click.group(cls=DefaultGroup, default="lock", default_if_no_args=True)
def main():
    """To get help for subcommands, use the conda-lock <SUBCOMMAND> --help"""
    pass


@main.command("lock")
@click.option(
    "--conda", default=None, help="path (or name) of the conda/mamba executable to use."
)
@click.option(
    "--mamba/--no-mamba", default=False, help="don't attempt to use or install mamba."
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
    help="Template for the lock file names. Filename must include {platform} token, and must not end in '.yml'. For a full list and description of available tokens, see the command help text.",
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
    default=[],
    type=str,
    multiple=True,
    help="When used in conjunction with input sources that support extras (pyproject.toml) will add the deps from those extras to the input specification",
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
def lock(
    conda,
    mamba,
    micromamba,
    platform,
    channel_overrides,
    dev_dependencies,
    files,
    kind,
    filename_template,
    strip_auth,
    extras,
    check_input_hash: bool,
    log_level,
    pdb,
    virtual_package_spec,
    update=None,
):
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

    if pdb:

        def handle_exception(exc_type, exc_value, exc_traceback):
            import pdb

            pdb.post_mortem(exc_traceback)

        sys.excepthook = handle_exception

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

    files = [pathlib.Path(file) for file in files]
    extras = set(extras)
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
        extras=extras,
        virtual_package_spec=virtual_package_spec,
        update=update,
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
    "--mamba/--no-mamba", default=False, help="don't attempt to use or install mamba."
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
    "--validate-platform",
    is_flag=True,
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
    help="include dev dependencies in the lockfile (where applicable)",
)
@click.option(
    "-E",
    "--extras",
    multiple=True,
    default=[],
    help="include dev dependencies in the lockfile (where applicable)",
)
@click.argument("lock-file")
def install(
    conda,
    mamba,
    micromamba,
    prefix,
    name,
    lock_file,
    auth,
    auth_file,
    validate_platform,
    log_level,
    dev,
    extras,
):
    """Perform a conda install"""
    logging.basicConfig(level=log_level)
    auth = json.loads(auth) if auth else read_json(auth_file) if auth_file else None
    _conda_exe = determine_conda_executable(conda, mamba=mamba, micromamba=micromamba)
    install_func = partial(do_conda_install, conda=_conda_exe, prefix=prefix, name=name)
    if validate_platform and not lock_file.endswith(".toml"):
        lockfile = read_file(lock_file)
        try:
            do_validate_platform(lockfile)
        except PlatformValidationError as error:
            raise PlatformValidationError(
                error.args[0] + " Disable validation with `--validate-platform=False`."
            )
    with _render_lockfile_for_install(
        lock_file, include_dev_dependencies=dev, extras=extras
    ) as lockfile:
        if auth:
            with _add_auth(read_file(lockfile), auth) as lockfile_with_auth:
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
    type=str,
    multiple=True,
    help="Kind of lock file(s) to generate [should be one of 'explicit' or 'env'].",
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
@click.argument("lock-file")
def render(
    dev_dependencies,
    kind,
    filename_template,
    extras,
    log_level,
    lock_file,
    pdb,
):
    """Render multi-platform lockfile into single-platform env or explicit file"""
    logging.basicConfig(level=log_level)

    if pdb:

        def handle_exception(exc_type, exc_value, exc_traceback):
            import pdb

            pdb.post_mortem(exc_traceback)

        sys.excepthook = handle_exception

    with open(lock_file) as f:
        lockfile = toml.load(f)

    do_render(
        lockfile,
        filename_template=filename_template,
        kinds=kind,
        include_dev_dependencies=dev_dependencies,
        extras=extras,
    )


if __name__ == "__main__":
    main()
