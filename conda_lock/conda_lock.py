"""
Somewhat hacky solution to create conda lock files.
"""

import datetime
import importlib.util
import itertools
import logging
import os
import pathlib
import posixpath
import re
import sys
import tempfile

from contextlib import contextmanager
from functools import partial
from importlib.metadata import distribution
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
import yaml

from ensureconda.api import ensureconda
from ensureconda.resolve import platform_subdir
from typing_extensions import Literal

from conda_lock.click_helpers import OrderedGroup
from conda_lock.common import (
    read_file,
    read_json,
    relative_path,
    temporary_file_with_contents,
    warn,
    write_file,
)
from conda_lock.conda_solver import solve_conda
from conda_lock.errors import MissingEnvVarError, PlatformValidationError
from conda_lock.export_lock_spec import EditableDependency, render_pixi_toml
from conda_lock.invoke_conda import (
    PathLike,
    _invoke_conda,
    determine_conda_executable,
    is_micromamba,
)
from conda_lock.lockfile import (
    parse_conda_lock_file,
    write_conda_lock_file,
)
from conda_lock.lockfile.v2prelim.models import (
    GitMeta,
    InputMeta,
    LockedDependency,
    Lockfile,
    LockMeta,
    MetadataOption,
    TimeMeta,
    UpdateSpecification,
)
from conda_lock.lookup import DEFAULT_MAPPING_URL
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import LockSpecification
from conda_lock.models.pip_repository import PipRepository
from conda_lock.pypi_solver import solve_pypi
from conda_lock.src_parser import make_lock_spec
from conda_lock.virtual_package import (
    FakeRepoData,
    default_virtual_package_repodata,
    virtual_package_repo_from_specification,
)


logger = logging.getLogger(__name__)
DEFAULT_FILES = [pathlib.Path("environment.yml"), pathlib.Path("environment.yaml")]

# Captures basic auth credentials, if they exists, in the third capture group.
AUTH_PATTERN = re.compile(r"^(# pip .* @ )?(https?:\/\/)(.*:.*@)?(.*)")

# Do not substitute in comments, but do substitute in pip installable packages
# with the pattern: # pip package @ url.
PKG_PATTERN = re.compile(r"(^[^#@].*|^# pip .*)")

# Captures the domain in the third group.
DOMAIN_PATTERN = re.compile(r"^(# pip .* @ )?(https?:\/\/)?([^\/]+)(.*)")

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

_implicit_cuda_message = """
  'cudatoolkit' package added implicitly without specifying that cuda packages
  should be accepted.
  Specify a cuda version via `--with-cuda VERSION` or via virtual packages
  to suppress this warning,
  or pass `--without-cuda` to explicitly exclude cuda packages.
"""


class UnknownLockfileKind(ValueError):
    pass


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
    conda: PathLike,
    prefix: "str | None",
    name: "str | None",
    file: pathlib.Path,
    copy: bool,
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

    env_prefix = ["env"] if kind == "env" and not is_micromamba(conda) else []
    copy_arg = ["--copy"] if kind != "env" and copy else []
    yes_arg = ["--yes"] if kind != "env" else []

    _conda(
        [
            *env_prefix,
            "create",
            "--quiet",
            *copy_arg,
            "--file",
            str(file),
            *yes_arg,
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


def make_lock_files(  # noqa: C901
    *,
    conda: PathLike,
    src_files: List[pathlib.Path],
    kinds: Sequence[TKindAll],
    lockfile_path: Optional[pathlib.Path] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    channel_overrides: Optional[Sequence[str]] = None,
    virtual_package_spec: Optional[pathlib.Path] = None,
    update: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
    filter_categories: bool = False,
    extras: Optional[AbstractSet[str]] = None,
    check_input_hash: bool = False,
    metadata_choices: AbstractSet[MetadataOption] = frozenset(),
    metadata_yamls: Sequence[pathlib.Path] = (),
    with_cuda: Optional[str] = None,
    strip_auth: bool = False,
    mapping_url: str,
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
    filename_template :
        Format for names of rendered explicit or env files. Must include {platform}.
    extras :
        Include the given extras in explicit or env output
    filter_categories :
        Filter out unused categories prior to solving
    check_input_hash :
        Do not re-solve for each target platform for which specifications are unchanged
    metadata_choices:
        Set of selected metadata fields to generate for this lockfile.
    with_cuda:
        The version of cuda requested.
        '' means no cuda.
        None will pick a default version and warn if cuda packages are installed.
    metadata_yamls:
        YAML or JSON file(s) containing structured metadata to add to metadata section of the lockfile.
    """

    # Compute lock specification
    required_categories = {"main"}
    if extras is not None:
        required_categories.update(extras)
    lock_spec = make_lock_spec(
        src_files=src_files,
        channel_overrides=channel_overrides,
        platform_overrides=platform_overrides,
        required_categories=required_categories if filter_categories else None,
        mapping_url=mapping_url,
    )

    # Load existing lockfile if it exists
    original_lock_content: Optional[Lockfile] = None
    if lockfile_path is None:
        lockfile_path = pathlib.Path(DEFAULT_LOCKFILE_NAME)
    if lockfile_path.exists():
        try:
            original_lock_content = parse_conda_lock_file(lockfile_path)
        except (yaml.error.YAMLError, FileNotFoundError):
            logger.warning("Failed to parse existing lock.  Regenerating from scratch")
            original_lock_content = None
    else:
        original_lock_content = None

    # initialize virtual packages
    if virtual_package_spec and virtual_package_spec.exists():
        virtual_package_repo = virtual_package_repo_from_specification(
            virtual_package_spec
        )
        cuda_specified = True
    else:
        if with_cuda is None:
            cuda_specified = False
            with_cuda = "11.4"
        else:
            cuda_specified = True
        virtual_package_repo = default_virtual_package_repodata(cuda_version=with_cuda)

    with virtual_package_repo:
        platforms_to_lock: List[str] = []
        platforms_already_locked: List[str] = []
        if original_lock_content is not None:
            platforms_already_locked = list(original_lock_content.metadata.platforms)
            if update is not None:
                # Narrow `update` sequence to list for mypy
                update = list(update)
            update_spec = UpdateSpecification(
                locked=original_lock_content.package, update=update
            )
            for platform in lock_spec.platforms:
                if (
                    update
                    or platform not in platforms_already_locked
                    or not check_input_hash
                    or lock_spec.content_hash_for_platform(
                        platform, virtual_package_repo
                    )
                    != original_lock_content.metadata.content_hash[platform]
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

        if not platforms_to_lock:
            new_lock_content = original_lock_content
        else:
            print(f"Locking dependencies for {platforms_to_lock}...", file=sys.stderr)

            fresh_lock_content = create_lockfile_from_spec(
                conda=conda,
                spec=lock_spec,
                platforms=platforms_to_lock,
                lockfile_path=lockfile_path,
                update_spec=update_spec,
                metadata_choices=metadata_choices,
                metadata_yamls=metadata_yamls,
                strip_auth=strip_auth,
                virtual_package_repo=virtual_package_repo,
                mapping_url=mapping_url,
            )

            if not original_lock_content:
                new_lock_content = fresh_lock_content
            else:
                # Persist packages from original lockfile for platforms not requested for lock
                packages_not_to_lock = [
                    dep
                    for dep in original_lock_content.package
                    if dep.platform not in platforms_to_lock
                ]
                lock_content_to_persist = original_lock_content.model_copy(
                    deep=True,
                    update={"package": packages_not_to_lock},
                )
                new_lock_content = lock_content_to_persist.merge(fresh_lock_content)

            if "lock" in kinds:
                write_conda_lock_file(
                    new_lock_content,
                    lockfile_path,
                    metadata_choices=metadata_choices,
                )
                print(
                    " - Install lock using:",
                    KIND_USE_TEXT["lock"].format(lockfile=str(lockfile_path)),
                    file=sys.stderr,
                )

        # After this point, we're working with `new_lock_content`, never
        # `original_lock_content` or `fresh_lock_content`.
        assert new_lock_content is not None

        # check for implicit inclusion of cudatoolkit
        # warn if it was pulled in, but not requested explicitly

        if not cuda_specified:
            # asking for 'cudatoolkit' is explicit enough
            cudatoolkit_requested = any(
                pkg.name == "cudatoolkit"
                for pkg in itertools.chain(*lock_spec.dependencies.values())
            )
            if not cudatoolkit_requested:
                for package in new_lock_content.package:
                    if package.name == "cudatoolkit":
                        logger.warning(_implicit_cuda_message)
                        break

        do_render(
            new_lock_content,
            kinds=[k for k in kinds if k != "lock"],
            filename_template=filename_template,
            extras=extras,
            check_input_hash=check_input_hash,
        )


def do_render(
    lockfile: Lockfile,
    kinds: Sequence[Union[Literal["env"], Literal["explicit"]]],
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
                    "input-hash": lockfile.metadata.content_hash,
                    "version": distribution("conda_lock").version,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime(
                        "%Y%m%dT%H%M%SZ"
                    ),
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
    extras: Optional[AbstractSet[str]],
    kind: Union[Literal["env"], Literal["explicit"]],
    platform: str,
    suppress_warning_for_pip_and_explicit: bool = False,
) -> List[str]:
    """
    Render lock content into a single-platform lockfile that can be installed
    with conda.

    Parameters
    ----------
    lockfile :
        Locked package versions
    extras :
        Optional dependency groups to include in output
    kind :
        Lockfile format (explicit or env)
    platform :
        Target platform
    suppress_warning_for_pip_and_explicit :
        When rendering internally for `conda-lock install`, we should suppress
        the warning about pip dependencies not being supported by all tools.
    """
    lockfile_contents = [
        "# Generated by conda-lock.",
        f"# platform: {platform}",
        f"# input_hash: {lockfile.metadata.content_hash.get(platform)}\n",
    ]

    categories_to_install: Set[str] = {
        "main",
        *(extras or []),
    }

    conda_deps: List[LockedDependency] = []
    pip_deps: List[LockedDependency] = []

    # ensure consistent ordering of generated file
    # topographic for explicit files and alphabetical otherwise (see gh #554)
    if kind == "explicit":
        lockfile.toposort_inplace()
    else:
        lockfile.alphasort_inplace()
    lockfile.filter_virtual_packages_inplace()

    for p in lockfile.package:
        if p.platform == platform and len(p.categories & categories_to_install) > 0:
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
            s = f"{spec.name} == {spec.version}"
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
                    f"      - {format_pip_requirement(dep, platform, direct=False)}"
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

        if len(pip_deps) > 0 and not suppress_warning_for_pip_and_explicit:
            logger.warning(
                "WARNING: installation of pip dependencies from explicit lockfiles "
                "is only supported by the "
                "'conda-lock install' and 'micromamba install' commands. Other tools "
                "may silently ignore them. For portability, we recommend using the "
                "newer unified lockfile format (i.e. removing the --kind=explicit "
                "argument."
            )
    else:
        raise ValueError(f"Unrecognised lock kind {kind}.")

    logging.debug("lockfile_contents:\n%s\n", lockfile_contents)
    return lockfile_contents


def _solve_for_arch(
    *,
    conda: PathLike,
    spec: LockSpecification,
    platform: str,
    channels: List[Channel],
    pip_repositories: List[PipRepository],
    virtual_package_repo: FakeRepoData,
    update_spec: Optional[UpdateSpecification] = None,
    strip_auth: bool = False,
    mapping_url: str,
) -> List[LockedDependency]:
    """
    Solve specification for a single platform
    """
    if update_spec is None:
        update_spec = UpdateSpecification()

    dependencies = spec.dependencies[platform]
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
        mapping_url=mapping_url,
    )

    if requested_deps_by_name["pip"]:
        if "python" not in conda_deps:
            raise ValueError("Got pip specs without Python")
        pip_deps = solve_pypi(
            pip_specs=requested_deps_by_name["pip"],
            use_latest=update_spec.update,
            pip_locked={
                dep.name: dep for dep in update_spec.locked if dep.manager == "pip"
            },
            conda_locked={dep.name: dep for dep in conda_deps.values()},
            python_version=conda_deps["python"].version,
            platform=platform,
            platform_virtual_packages=(
                virtual_package_repo.all_repodata.get(platform, {"packages": None})[
                    "packages"
                ]
                if virtual_package_repo
                else None
            ),
            pip_repositories=pip_repositories,
            allow_pypi_requests=spec.allow_pypi_requests,
            strip_auth=strip_auth,
            mapping_url=mapping_url,
        )
    else:
        pip_deps = {}

    return list(conda_deps.values()) + list(pip_deps.values())


def convert_structured_metadata_yaml(in_path: pathlib.Path) -> Dict[str, Any]:
    with in_path.open("r") as infile:
        metadata = yaml.safe_load(infile)
    return metadata


def update_metadata(to_change: Dict[str, Any], change_source: Dict[str, Any]) -> None:
    for key in change_source:
        if key in to_change:
            logger.warning(
                f"Custom metadata field {key} provided twice, overwriting value "
                + f"{to_change[key]} with {change_source[key]}"
            )
    to_change.update(change_source)


def get_custom_metadata(
    metadata_yamls: Sequence[pathlib.Path],
) -> Optional[Dict[str, str]]:
    custom_metadata_dict: Dict[str, str] = {}
    for yaml_path in metadata_yamls:
        new_metadata = convert_structured_metadata_yaml(yaml_path)
        update_metadata(custom_metadata_dict, new_metadata)
    if custom_metadata_dict:
        return custom_metadata_dict
    return None


def create_lockfile_from_spec(
    *,
    conda: PathLike,
    spec: LockSpecification,
    platforms: Optional[List[str]] = None,
    lockfile_path: pathlib.Path,
    update_spec: Optional[UpdateSpecification] = None,
    metadata_choices: AbstractSet[MetadataOption] = frozenset(),
    metadata_yamls: Sequence[pathlib.Path] = (),
    strip_auth: bool = False,
    virtual_package_repo: FakeRepoData,
    mapping_url: str,
) -> Lockfile:
    """
    Solve or update specification
    """
    if platforms is None:
        platforms = []

    locked: Dict[Tuple[str, str, str], LockedDependency] = {}

    for platform in platforms or spec.platforms:
        deps = _solve_for_arch(
            conda=conda,
            spec=spec,
            platform=platform,
            channels=[*spec.channels, virtual_package_repo.channel],
            pip_repositories=spec.pip_repositories,
            virtual_package_repo=virtual_package_repo,
            update_spec=update_spec,
            strip_auth=strip_auth,
            mapping_url=mapping_url,
        )

        for dep in deps:
            locked[(dep.manager, dep.name, dep.platform)] = dep

    meta_sources: Dict[str, pathlib.Path] = {}
    for source in spec.sources:
        try:
            path = relative_path(lockfile_path.parent, source)
        except ValueError as e:
            if "Paths don't have the same drive" not in str(e):
                raise e
            path = str(source.resolve())
        meta_sources[path] = source

    if MetadataOption.TimeStamp in metadata_choices:
        time_metadata = TimeMeta.create()
    else:
        time_metadata = None

    if metadata_choices & {
        MetadataOption.GitUserEmail,
        MetadataOption.GitUserName,
        MetadataOption.GitSha,
    }:
        if not importlib.util.find_spec("git"):
            raise RuntimeError(
                "The GitPython package is required to read Git metadata."
            )
        git_metadata = GitMeta.create(
            metadata_choices=metadata_choices,
            src_files=spec.sources,
        )
    else:
        git_metadata = None

    if metadata_choices & {MetadataOption.InputSha, MetadataOption.InputMd5}:
        inputs_metadata: Optional[Dict[str, InputMeta]] = {
            meta_src: InputMeta.create(
                metadata_choices=metadata_choices, src_file=src_file
            )
            for meta_src, src_file in meta_sources.items()
        }
    else:
        inputs_metadata = None

    custom_metadata = get_custom_metadata(metadata_yamls=metadata_yamls)
    content_hash = spec.content_hash(virtual_package_repo)

    return Lockfile(
        package=[locked[k] for k in locked],
        metadata=LockMeta(
            content_hash=content_hash,
            channels=[c for c in spec.channels],
            platforms=spec.platforms,
            sources=list(meta_sources.keys()),
            git_metadata=git_metadata,
            time_metadata=time_metadata,
            inputs_metadata=inputs_metadata,
            custom_metadata=custom_metadata,
        ),
    )


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
        _add_auth_to_line(line, auth) if PKG_PATTERN.match(line) else line
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
    return AUTH_PATTERN.sub(r"\1\2\4", line)


def _extract_domain(line: str) -> str:
    return DOMAIN_PATTERN.sub(r"\3", line)


def _strip_auth_from_lockfile(lockfile: str) -> str:
    lockfile_lines = lockfile.strip().split("\n")
    stripped_lockfile_lines = tuple(
        _strip_auth_from_line(line) if PKG_PATTERN.match(line) else line
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
    extras: Optional[AbstractSet[str]] = None,
    force_platform: Optional[str] = None,
) -> Iterator[pathlib.Path]:
    """
    Render lock content into a temporary, explicit lockfile for the current platform

    Parameters
    ----------
    filename :
        Path to conda-lock.yml
    extras :
        Optional dependency groups to include in output

    """
    kind = _detect_lockfile_kind(pathlib.Path(filename))
    if kind in ("explicit", "env"):
        yield filename
        return

    lock_content = parse_conda_lock_file(pathlib.Path(filename))

    platform = force_platform or platform_subdir()

    if platform not in lock_content.metadata.platforms:
        suggested_platforms_section = "platforms:\n- "
        suggested_platforms_section += "\n- ".join(
            [platform, *lock_content.metadata.platforms]
        )
        suggested_platform_args = "--platform=" + " --platform=".join(
            [platform, *lock_content.metadata.platforms]
        )
        raise PlatformValidationError(
            f"The lockfile {filename} does not contain a solution for the current "
            f"platform {platform}. The lockfile only contains solutions for the "
            f"following platforms: {', '.join(lock_content.metadata.platforms)}. In "
            f"order to add support for {platform}, you must regenerate the lockfile. "
            f"Either add the following section to your environment.yml:\n\n"
            f"{suggested_platforms_section}\n\n"
            f"or add the following arguments to the conda-lock command:\n\n"
            f"{suggested_platform_args}\n\n"
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
        extras=extras,
        suppress_warning_for_pip_and_explicit=True,
    )
    with temporary_file_with_contents("\n".join(content) + "\n") as path:
        yield path


def _detect_lockfile_kind(path: pathlib.Path) -> TKindAll:
    content = path.read_text(encoding="utf-8")
    if "@EXPLICIT" in {line.strip() for line in content.splitlines()}:
        return "explicit"
    try:
        lockfile = yaml.safe_load(content)
        if {"channels", "dependencies"} <= set(lockfile):
            return "env"
        if "version" in lockfile:
            # Version validation is handled by `lockfile.parse_conda_lock_file`
            return "lock"
        raise UnknownLockfileKind(f"Could not detect the kind of lockfile at {path}")
    except yaml.YAMLError:
        raise UnknownLockfileKind(
            f"Could not detect the kind of lockfile at {path}. Note that explicit "
            "lockfiles must contain the line '@EXPLICIT'."
        )


_deprecated_dev_help = (
    "(DEPRECATED) include (or not) dev dependencies in the lockfile (where applicable)"
)


def _deprecated_dev_cli(ctx: click.Context, param: click.Parameter, value: Any) -> Any:
    """A click callback function raising a deprecation error."""
    if value:
        raise click.BadParameter(
            "--dev-dependencies/--no-dev-dependencies (lock, render) and --dev/--no-dev (install) "
            "switches are deprecated. Use `--extra dev` instead."
        )
    else:
        return value


def handle_no_specified_source_files(
    lockfile_path: Optional[pathlib.Path],
) -> List[pathlib.Path]:
    """No sources were specified on the CLI, so try to read them from the lockfile.

    If none are found, then fall back to the default files.
    """
    if lockfile_path is None:
        lockfile_path = pathlib.Path(DEFAULT_LOCKFILE_NAME)
    if lockfile_path.exists():
        lock_content = parse_conda_lock_file(lockfile_path)
        # reconstruct native paths
        locked_environment_files = [
            (
                pathlib.Path(p)
                # absolute paths could be locked for both flavours
                if pathlib.PurePosixPath(p).is_absolute()
                or pathlib.PureWindowsPath(p).is_absolute()
                else pathlib.Path(
                    pathlib.PurePosixPath(lockfile_path).parent
                    / pathlib.PurePosixPath(p)
                )
            )
            for p in lock_content.metadata.sources
        ]
        if all(p.exists() for p in locked_environment_files):
            environment_files = locked_environment_files
            logger.warning(
                f"Using source files {[str(p) for p in locked_environment_files]} "
                f"from {lockfile_path} to create the environment."
            )
        else:
            missing = [p for p in locked_environment_files if not p.exists()]
            environment_files = DEFAULT_FILES.copy()
            print(
                f"{lockfile_path} was created from {[str(p) for p in locked_environment_files]},"
                f" but some files ({[str(p) for p in missing]}) do not exist. Falling back to"
                f" {[str(p) for p in environment_files]}.",
                file=sys.stderr,
            )
    else:
        # No lockfile provided, so fall back to the default files
        environment_files = [f for f in DEFAULT_FILES if f.exists()]
        if len(environment_files) == 0:
            logger.error(
                "No source files provided and no default files found. Exiting."
            )
            sys.exit(1)
        elif len(environment_files) > 1:
            logger.error(f"Multiple default files found: {environment_files}. Exiting.")
            sys.exit(1)
    return environment_files


def run_lock(
    environment_files: List[pathlib.Path],
    *,
    conda_exe: Optional[PathLike],
    platforms: Optional[Sequence[str]] = None,
    mamba: bool = False,
    micromamba: bool = False,
    channel_overrides: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
    kinds: Optional[Sequence[TKindAll]] = None,
    lockfile_path: Optional[pathlib.Path] = None,
    check_input_hash: bool = False,
    extras: Optional[AbstractSet[str]] = None,
    virtual_package_spec: Optional[pathlib.Path] = None,
    with_cuda: Optional[str] = None,
    update: Optional[Sequence[str]] = None,
    filter_categories: bool = False,
    metadata_choices: AbstractSet[MetadataOption] = frozenset(),
    metadata_yamls: Sequence[pathlib.Path] = (),
    strip_auth: bool = False,
    mapping_url: str,
) -> None:
    if len(environment_files) == 0:
        environment_files = handle_no_specified_source_files(lockfile_path)

    _conda_exe = determine_conda_executable(
        conda_exe, mamba=mamba, micromamba=micromamba
    )
    logger.debug(f"Using conda executable: {_conda_exe}")
    make_lock_files(
        conda=_conda_exe,
        src_files=environment_files,
        platform_overrides=platforms,
        channel_overrides=channel_overrides,
        virtual_package_spec=virtual_package_spec,
        with_cuda=with_cuda,
        update=update,
        kinds=kinds or DEFAULT_KINDS,
        lockfile_path=lockfile_path,
        filename_template=filename_template,
        extras=extras,
        check_input_hash=check_input_hash,
        filter_categories=filter_categories,
        metadata_choices=metadata_choices,
        metadata_yamls=metadata_yamls,
        strip_auth=strip_auth,
        mapping_url=mapping_url,
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

CONTEXT_SETTINGS = {"show_default": True, "help_option_names": ["--help", "-h"]}


@main.command("lock", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--conda",
    default=None,
    help="path (or name) of the conda/mamba executable to use.",
    envvar="CONDA_LOCK_CONDA",
)
@click.option(
    "--mamba/--no-mamba",
    default=HAVE_MAMBA,
    help="don't attempt to use or install mamba.",
    envvar="CONDA_LOCK_MAMBA",
)
@click.option(
    "--micromamba/--no-micromamba",
    default=False,
    help="don't attempt to use or install micromamba.",
    envvar="CONDA_LOCK_MICROMAMBA",
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
    "--dev-dependencies",
    "--no-dev-dependencies",
    "dev_dependencies",
    is_flag=True,
    flag_value=True,
    default=False,
    help=_deprecated_dev_help,
    hidden=False,
    is_eager=True,
    callback=_deprecated_dev_cli,
)
@click.option(
    "-f",
    "--file",
    "files",
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
    default=None,
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
@click.option(
    "--md",
    "--metadata",
    "metadata_choices",
    default=[],
    multiple=True,
    type=click.Choice([md.value for md in MetadataOption]),
    help="Metadata fields to include in lock-file",
)
@click.option(
    "--with-cuda",
    "with_cuda",
    type=str,
    default=None,
    help="Specify cuda version to use in virtual packages. Avoids warning about implicit acceptance of cuda dependencies. Ignored if virtual packages are specified.",
)
@click.option(
    "--without-cuda",
    "with_cuda",
    flag_value="",
    default=None,
    help="Disable cuda in virtual packages. Prevents accepting cuda variants of packages. Ignored if virtual packages are specified.",
)
@click.option(
    "--mdy",
    "--metadata-yaml",
    "--metadata-json",
    "metadata_yamls",
    default=[],
    multiple=True,
    type=click.Path(),
    help="YAML or JSON file(s) containing structured metadata to add to metadata section of the lockfile.",
)
@click.pass_context
def lock(
    ctx: click.Context,
    conda: Optional[str],
    mamba: bool,
    micromamba: bool,
    platform: Sequence[str],
    channel_overrides: Sequence[str],
    files: Sequence[PathLike],
    kind: Sequence[Union[Literal["lock"], Literal["env"], Literal["explicit"]]],
    filename_template: str,
    lockfile: Optional[PathLike],
    strip_auth: bool,
    extras: Sequence[str],
    filter_categories: bool,
    check_input_hash: bool,
    log_level: TLogLevel,
    pdb: bool,
    virtual_package_spec: Optional[PathLike],
    pypi_to_conda_lookup_file: Optional[str],
    with_cuda: Optional[str] = None,
    update: Optional[Sequence[str]] = None,
    metadata_choices: Sequence[str] = (),
    metadata_yamls: Sequence[PathLike] = (),
    dev_dependencies: bool = False,  # DEPRECATED
) -> None:
    """Generate fully reproducible lock files for conda environments.

    By default, a multi-platform lock file is written to conda-lock.yml.

    When choosing the "explicit" or "env" kind, lock files are written to
    conda-{platform}.lock. These filenames can be customized using the
    --filename-template argument. The following tokens are available:

    \b
        platform: The platform this lock file was generated for (conda subdir).
        input-hash: A sha256 hash of the lock file input specification.
        version: The version of conda-lock used to generate this lock file.
        timestamp: The approximate timestamp of the output file in ISO8601 basic format.
    """
    logging.basicConfig(level=log_level)

    # Set Pypi <--> Conda lookup file location
    mapping_url = (
        DEFAULT_MAPPING_URL
        if pypi_to_conda_lookup_file is None
        else pypi_to_conda_lookup_file
    )

    metadata_enum_choices = set(MetadataOption(md) for md in metadata_choices)

    environment_files = [pathlib.Path(file) for file in files]

    if pdb:
        sys.excepthook = _handle_exception_post_mortem

    if virtual_package_spec is None:
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

    extras_ = set(extras)
    lock_func = partial(
        run_lock,
        environment_files=environment_files,
        conda_exe=conda,
        platforms=platform,
        mamba=mamba,
        micromamba=micromamba,
        channel_overrides=channel_overrides,
        kinds=kind,
        lockfile_path=None if lockfile is None else pathlib.Path(lockfile),
        extras=extras_,
        virtual_package_spec=virtual_package_spec,
        with_cuda=with_cuda,
        update=update,
        filter_categories=filter_categories,
        metadata_choices=metadata_enum_choices,
        metadata_yamls=[pathlib.Path(path) for path in metadata_yamls],
        strip_auth=strip_auth,
        mapping_url=mapping_url,
    )
    if strip_auth:
        with tempfile.TemporaryDirectory() as tempdir:
            filename_template_temp = f"{tempdir}/{filename_template.split('/')[-1]}"
            lock_func(filename_template=filename_template_temp)
            filename_template_dir = "/".join(filename_template.split("/")[:-1])
            for file in os.listdir(tempdir):
                lockfile_content = read_file(os.path.join(tempdir, file))
                lockfile_content = _strip_auth_from_lockfile(lockfile_content)
                write_file(lockfile_content, os.path.join(filename_template_dir, file))
    else:
        lock_func(
            filename_template=filename_template, check_input_hash=check_input_hash
        )


DEFAULT_INSTALL_OPT_MAMBA = HAVE_MAMBA
DEFAULT_INSTALL_OPT_MICROMAMBA = False
DEFAULT_INSTALL_OPT_COPY = False
DEFAULT_INSTALL_OPT_VALIDATE_PLATFORM = True
DEFAULT_INSTALL_OPT_LOG_LEVEL = "INFO"
DEFAULT_INSTALL_OPT_LOCK_FILE = pathlib.Path(DEFAULT_LOCKFILE_NAME)


@main.command("install", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--conda",
    default=None,
    help="path (or name) of the conda/mamba executable to use.",
    envvar="CONDA_LOCK_CONDA",
)
@click.option(
    "--mamba/--no-mamba",
    default=DEFAULT_INSTALL_OPT_MAMBA,
    help="don't attempt to use or install mamba.",
    envvar="CONDA_LOCK_MAMBA",
)
@click.option(
    "--micromamba/--no-micromamba",
    default=DEFAULT_INSTALL_OPT_MICROMAMBA,
    help="don't attempt to use or install micromamba.",
    envvar="CONDA_LOCK_MICROMAMBA",
)
@click.option(
    "--copy",
    is_flag=True,
    default=DEFAULT_INSTALL_OPT_COPY,
    help=(
        "Install using `--copy` to prevent links. "
        "This is useful for building containers"
    ),
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
    default=DEFAULT_INSTALL_OPT_VALIDATE_PLATFORM,
    help="Whether the platform compatibility between your lockfile and the host system should be validated.",
)
@click.option(
    "--log-level",
    help="Log level.",
    default=DEFAULT_INSTALL_OPT_LOG_LEVEL,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
)
@click.option(
    "--dev",
    "--no-dev",
    "dev",
    is_flag=True,
    flag_value=True,
    default=False,
    help=_deprecated_dev_help,
    hidden=False,
    is_eager=True,
    callback=_deprecated_dev_cli,
)
@click.option(
    "-E",
    "--extras",
    multiple=True,
    default=[],
    help="include extra dependencies from the lockfile (where applicable)",
)
@click.option(
    "--force-platform",
    help="Force using the given platform when installing from the lockfile, instead of the native platform.",
    default=platform_subdir,
)
@click.argument("lock-file", default=DEFAULT_INSTALL_OPT_LOCK_FILE, type=click.Path())
@click.pass_context
def click_install(
    ctx: click.Context,
    conda: Optional[str],
    mamba: bool,
    micromamba: bool,
    copy: bool,
    prefix: Optional[str],
    name: Optional[str],
    lock_file: pathlib.Path,
    auth: Optional[str],
    auth_file: Optional[PathLike],
    validate_platform: bool,
    log_level: TLogLevel,
    extras: List[str],
    force_platform: str,
    dev: bool,  # DEPRECATED
) -> None:
    # bail out if we do not encounter the lockfile
    lock_file = pathlib.Path(lock_file)
    if not lock_file.exists():
        print(ctx.get_help())
        sys.exit(1)

    """Perform a conda install"""
    logging.basicConfig(level=log_level)
    install(
        conda=conda,
        mamba=mamba,
        micromamba=micromamba,
        copy=copy,
        prefix=prefix,
        name=name,
        lock_file=lock_file,
        auth=auth,
        auth_file=auth_file,
        validate_platform=validate_platform,
        extras=extras,
        force_platform=force_platform,
    )


def install(
    conda: Optional[str] = None,
    mamba: bool = DEFAULT_INSTALL_OPT_MAMBA,
    micromamba: bool = DEFAULT_INSTALL_OPT_MICROMAMBA,
    copy: bool = DEFAULT_INSTALL_OPT_COPY,
    prefix: Optional[str] = None,
    name: Optional[str] = None,
    lock_file: pathlib.Path = DEFAULT_INSTALL_OPT_LOCK_FILE,
    auth: Optional[str] = None,
    auth_file: Optional[PathLike] = None,
    validate_platform: bool = DEFAULT_INSTALL_OPT_VALIDATE_PLATFORM,
    extras: Optional[List[str]] = None,
    force_platform: Optional[str] = None,
) -> None:
    if extras is None:
        extras = []
    _auth = (
        yaml.safe_load(auth) if auth else read_json(auth_file) if auth_file else None
    )
    _conda_exe = determine_conda_executable(conda, mamba=mamba, micromamba=micromamba)
    install_func = partial(
        do_conda_install, conda=_conda_exe, prefix=prefix, name=name, copy=copy
    )
    if validate_platform and _detect_lockfile_kind(lock_file) != "lock":
        lockfile_contents = read_file(lock_file)
        try:
            do_validate_platform(lockfile_contents)
        except PlatformValidationError as error:
            raise PlatformValidationError(
                error.args[0] + " Disable validation with `--no-validate-platform`."
            )
    with _render_lockfile_for_install(
        lock_file,
        extras=set(extras),
        force_platform=force_platform,
    ) as lockfile:
        if _auth is not None:
            with _add_auth(read_file(lockfile), _auth) as lockfile_with_auth:
                install_func(file=lockfile_with_auth)
        else:
            install_func(file=lockfile)


@main.command("render", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--dev-dependencies",
    "--no-dev-dependencies",
    "dev_dependencies",
    is_flag=True,
    flag_value=True,
    default=False,
    help=_deprecated_dev_help,
    hidden=False,
    is_eager=True,
    callback=_deprecated_dev_cli,
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
    kind: Sequence[Union[Literal["env"], Literal["explicit"]]],
    filename_template: str,
    extras: List[str],
    log_level: TLogLevel,
    lock_file: PathLike,
    pdb: bool,
    platform: Sequence[str],
    dev_dependencies: bool,  # DEPRECATED
) -> None:
    """Render multi-platform lockfile into single-platform env or explicit file"""
    logging.basicConfig(level=log_level)

    if pdb:
        sys.excepthook = _handle_exception_post_mortem

    # bail out if we do not encounter the lockfile
    lock_file = pathlib.Path(lock_file)
    if not lock_file.exists():
        print(f"ERROR: Lockfile {lock_file} does not exist.\n\n", file=sys.stderr)
        print(ctx.get_help())
        sys.exit(1)

    lock_content = parse_conda_lock_file(lock_file)

    do_render(
        lock_content,
        filename_template=filename_template,
        kinds=kind,
        extras=set(extras),
        override_platform=platform,
    )


@main.command("render-lock-spec", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--conda",
    default=None,
    help="path (or name) of the conda/mamba executable to use.",
    hidden=True,
)
@click.option(
    "--mamba/--no-mamba",
    default=None,
    help="don't attempt to use or install mamba.",
    hidden=True,
)
@click.option(
    "--micromamba/--no-micromamba",
    default=None,
    help="don't attempt to use or install micromamba.",
    hidden=True,
)
@click.option(
    "-p",
    "--platform",
    multiple=True,
    help="render lock files for the following platforms",
)
@click.option(
    "-c",
    "--channel",
    "channel_overrides",
    multiple=True,
    help="""Override the channels to use when solving the environment. These will replace the channels as listed in the various source files.""",
)
@click.option(
    "--dev-dependencies",
    "--no-dev-dependencies",
    "dev_dependencies",
    is_flag=True,
    flag_value=True,
    default=False,
    help=_deprecated_dev_help,
    hidden=False,
    is_eager=True,
    callback=_deprecated_dev_cli,
)
@click.option(
    "-f",
    "--file",
    "files",
    type=click.Path(),
    multiple=True,
    help="path to a dependency specification, can be repeated",
)
@click.option(
    "-k",
    "--kind",
    type=click.Choice(["pixi.toml"]),
    multiple=True,
    help="Kind of lock specification to generate. Must be 'pixi.toml'.",
)
@click.option(
    "--filename-template",
    default=None,
    help="Template for single-platform (explicit, env) lock file names. Filename must include {platform} token, and must not end in '.yml'. For a full list and description of available tokens, see the command help text.",
    hidden=True,
)
@click.option(
    "--lockfile",
    default=None,
    help="Path to a conda-lock.yml which references source files to be used.",
)
@click.option(
    "--strip-auth",
    is_flag=True,
    default=None,
    help="Strip the basic auth credentials from the lockfile.",
    hidden=True,
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
    default=None,
    help="Check existing input hashes in lockfiles before regenerating lock files.  If no files were updated exit with exit code 4.  Incompatible with --strip-auth",
    hidden=True,
)
@click.option(
    "--stdout",
    is_flag=True,
    help="Print the lock specification to stdout.",
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
    hidden=True,
)
@click.option(
    "--update",
    multiple=True,
    help="Packages to update to their latest versions. If empty, update all.",
    hidden=True,
)
@click.option(
    "--pypi_to_conda_lookup_file",
    type=str,
    help="Location of the lookup file containing Pypi package names to conda names.",
)
@click.option(
    "--md",
    "--metadata",
    "metadata_choices",
    default=[],
    multiple=True,
    type=click.Choice([md.value for md in MetadataOption]),
    hidden=True,
)
@click.option(
    "--with-cuda",
    "with_cuda",
    type=str,
    default=None,
    help="Specify cuda version to use in the system requirements.",
)
@click.option(
    "--without-cuda",
    "with_cuda",
    flag_value="",
    default=None,
    help="Disable cuda in virtual packages. Prevents accepting cuda variants of packages. Ignored if virtual packages are specified.",
    hidden=True,
)
@click.option(
    "--mdy",
    "--metadata-yaml",
    "--metadata-json",
    "metadata_yamls",
    default=[],
    multiple=True,
    type=click.Path(),
    help="YAML or JSON file(s) containing structured metadata to add to metadata section of the lockfile.",
    hidden=True,
)
@click.option(
    "--pixi-project-name",
    type=str,
    default=None,
    help="Name of the Pixi project",
)
@click.option(
    "--editable",
    type=str,
    multiple=True,
    help="Add an editable pip dependency as name=path, e.g. --editable mypkg=./src/mypkg",
)
def render_lock_spec(  # noqa: C901
    conda: Optional[str],
    mamba: Optional[bool],
    micromamba: Optional[bool],
    platform: Sequence[str],
    channel_overrides: Sequence[str],
    files: Sequence[PathLike],
    kind: Sequence[Literal["pixi.toml"]],
    filename_template: Optional[str],
    lockfile: Optional[PathLike],
    strip_auth: bool,
    extras: Sequence[str],
    filter_categories: bool,
    check_input_hash: Optional[bool],
    log_level: TLogLevel,
    pdb: bool,
    virtual_package_spec: Optional[PathLike],
    pypi_to_conda_lookup_file: Optional[str],
    with_cuda: Optional[str],
    update: Sequence[str],
    metadata_choices: Sequence[str],
    metadata_yamls: Sequence[PathLike],
    stdout: bool,
    pixi_project_name: Optional[str],
    editable: Sequence[str],
    dev_dependencies: bool,  # DEPRECATED
) -> None:
    """Combine source files into a single lock specification"""
    kinds = set(kind)
    if kinds != {"pixi.toml"}:
        raise NotImplementedError(
            "Only 'pixi.toml' is supported at the moment. Add `--kind=pixi.toml`."
        )
    if pixi_project_name is not None and "pixi.toml" not in kinds:
        raise ValueError("The --pixi-project-name option is only valid for pixi.toml")
    if not stdout:
        raise NotImplementedError(
            "Only stdout is supported at the moment. Add `--stdout`."
        )
    if len(metadata_choices) > 0:
        warn(f"Metadata options {metadata_choices} will be ignored.")
    del metadata_choices
    if len(metadata_yamls) > 0:
        warn(f"Metadata files {metadata_yamls} will be ignored.")
    del metadata_yamls
    if virtual_package_spec:
        warn(
            f"Virtual package spec {virtual_package_spec} will be ignored. "
            f"Please add virtual packages by hand to the [system-requirements] table."
        )
    del virtual_package_spec
    if with_cuda is not None:
        if not isinstance(with_cuda, str) or with_cuda == "":
            raise NotImplementedError(
                "Please specify an explicit version to --with-cuda."
            )
    if update:
        warn(f"Update packages {update} will be ignored.")
    del update
    if conda is not None:
        warn(f"Conda executable {conda} will be ignored.")
    del conda
    if mamba is not None:
        warn(f"Mamba option {mamba} will be ignored.")
    del mamba
    if micromamba is not None:
        warn(f"Micromamba option {micromamba} will be ignored.")
    del micromamba
    if filename_template is not None:
        warn(f"Filename template {filename_template} will be ignored.")
    del filename_template
    if strip_auth:
        warn(f"Strip auth {strip_auth} will be ignored.")
    del strip_auth
    if check_input_hash is not None:
        warn(f"Check input hash {check_input_hash} will be ignored.")
    del check_input_hash
    if lockfile is not None:
        if len(files) > 0:
            raise ValueError(
                f"Don't specify the lockfile if source files {files} are "
                f"specified explicitly."
            )
        warn(
            f"It is recommended to specify lockfile sources explicitly "
            f"instead of via {lockfile}."
        )
        lockfile_path = pathlib.Path(lockfile)
    else:
        lockfile_path = None
    del lockfile
    editables: List[EditableDependency] = []
    for ed in editable:
        name_path = ed.split("=", maxsplit=1)
        if len(name_path) != 2:
            raise ValueError(
                f"Editable dependency must contain '=' to specify name=path, "
                f"but got {ed}."
            )
        name, path = name_path
        if not path.startswith("."):
            warn(
                f"Editable dependency path {path} should be relative to the project "
                f"root, but it does not start with '.'"
            )
        editables.append(EditableDependency(name=name, path=path))

    logging.basicConfig(level=log_level)

    # Set Pypi <--> Conda lookup file location
    mapping_url = (
        DEFAULT_MAPPING_URL
        if pypi_to_conda_lookup_file is None
        else pypi_to_conda_lookup_file
    )

    src_files = [pathlib.Path(file) for file in files]

    if pdb:
        sys.excepthook = _handle_exception_post_mortem

    do_render_lockspec(
        src_files=src_files,
        kinds=kinds,
        stdout=stdout,
        platform_overrides=platform,
        channel_overrides=channel_overrides,
        extras=set(extras),
        filter_categories=filter_categories,
        lockfile_path=lockfile_path,
        with_cuda=with_cuda,
        pixi_project_name=pixi_project_name,
        mapping_url=mapping_url,
        editables=editables,
    )


def do_render_lockspec(
    src_files: List[pathlib.Path],
    *,
    kinds: AbstractSet[Literal["pixi.toml"]],
    stdout: bool,
    platform_overrides: Optional[Sequence[str]] = None,
    channel_overrides: Optional[Sequence[str]] = None,
    extras: Optional[AbstractSet[str]] = None,
    filter_categories: bool = False,
    lockfile_path: Optional[pathlib.Path] = None,
    with_cuda: Optional[str] = None,
    pixi_project_name: Optional[str] = None,
    mapping_url: str,
    editables: Optional[List[EditableDependency]] = None,
) -> None:
    if len(src_files) == 0:
        src_files = handle_no_specified_source_files(lockfile_path)

    required_categories = {"main"}
    if extras is not None:
        required_categories.update(extras)
    lock_spec = make_lock_spec(
        src_files=src_files,
        channel_overrides=channel_overrides,
        platform_overrides=platform_overrides,
        required_categories=required_categories if filter_categories else None,
        mapping_url=mapping_url,
    )
    if "pixi.toml" in kinds:
        pixi_toml = render_pixi_toml(
            lock_spec=lock_spec,
            with_cuda=with_cuda,
            project_name=pixi_project_name,
            editables=editables,
        )
        if stdout:
            print(pixi_toml.as_string(), end="")
        else:
            raise NotImplementedError("Only stdout is supported at the moment.")


def _handle_exception_post_mortem(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_traceback: Optional[TracebackType],
) -> Any:
    import pdb

    pdb.post_mortem(exc_traceback)


if __name__ == "__main__":
    main()
