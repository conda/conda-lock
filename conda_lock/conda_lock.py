"""
Somewhat hacky solution to create conda lock files.
"""

import atexit
import datetime
import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from contextlib import contextmanager
from functools import partial
from itertools import chain
from typing import (
    Dict,
    Iterator,
    List,
    MutableSequence,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import click
import ensureconda
import pkg_resources

from click_default_group import DefaultGroup

from conda_lock.common import read_file, read_json, write_file
from conda_lock.errors import PlatformValidationError
from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml


DEFAULT_FILES = [pathlib.Path("environment.yml")]

PathLike = Union[str, pathlib.Path]

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
}
KIND_USE_TEXT = {
    "explicit": "conda create --name YOURENV --file {lockfile}",
    "env": "conda env create --name YOURENV --file {lockfile}",
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


def conda_pkgs_dir():
    global CONDA_PKGS_DIRS
    if CONDA_PKGS_DIRS is None:
        temp_dir = tempfile.TemporaryDirectory()
        CONDA_PKGS_DIRS = temp_dir.name
        atexit.register(temp_dir.cleanup)
        return CONDA_PKGS_DIRS
    else:
        return CONDA_PKGS_DIRS


def conda_env_override(platform) -> Dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "CONDA_SUBDIR": platform,
            "CONDA_PKGS_DIRS": conda_pkgs_dir(),
            "CONDA_UNSATISFIABLE_HINTS_CHECK_DEPTH": "0",
            "CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY": "False",
        }
    )
    return env


def solve_specs_for_arch(
    conda: PathLike, channels: Sequence[str], specs: List[str], platform: str
) -> dict:
    args: MutableSequence[PathLike] = [
        str(conda),
        "create",
        "--prefix",
        os.path.join(conda_pkgs_dir(), "prefix"),
        "--dry-run",
        "--json",
    ]
    conda_flags = os.environ.get("CONDA_FLAGS")
    if conda_flags:
        args.extend(shlex.split(conda_flags))
    if channels:
        args.append("--override-channels")
    for channel in channels:
        args.extend(["--channel", channel])
        if channel == "defaults" and platform in {"win-64", "win-32"}:
            # msys2 is a windows-only channel that conda automatically
            # injects if the host platform is Windows. If our host
            # platform is not Windows, we need to add it manually
            args.extend(["--channel", "msys2"])
    args.extend(specs)
    proc = subprocess.run(
        args,
        env=conda_env_override(platform),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    def print_proc(proc):
        print(f"    Command: {proc.args}")
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}")
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}")

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        try:
            err_json = json.loads(proc.stdout)
            message = err_json["message"]
        except json.JSONDecodeError as e:
            print(f"Failed to parse json, {e}")
            message = ""
        except KeyError:
            print("Message key not found in json! returning the full json text")
            message = err_json

        print(f"Could not lock the environment for platform {platform}")
        if message:
            print(message)
        print_proc(proc)

        sys.exit(1)

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("Could not solve for lock")
        print_proc(proc)
        sys.exit(1)


def _process_stdout(stdout):
    cache = set()
    extracting_packages = False
    leading_empty = True
    for logline in stdout:
        logline = logline.rstrip()
        if logline:
            leading_empty = False
        if logline == "Downloading and Extracting Packages":
            extracting_packages = True
        if not logline and (extracting_packages or leading_empty):
            continue
        if "%" in logline:
            logline = logline.split()[0]
            if logline not in cache:
                yield logline
                cache.add(logline)
        else:
            yield logline


def do_conda_install(conda: PathLike, prefix: str, name: str, file: str) -> None:

    if prefix and name:
        raise ValueError("Provide either prefix, or name, but not both.")

    kind = "env" if file.endswith(".yml") else "explicit"

    args: MutableSequence[PathLike] = [
        str(conda),
        *(["env"] if kind == "env" else []),
        "create",
        "--file",
        file,
        *([] if kind == "env" else ["--yes"]),
    ]

    if prefix:
        args.append("--prefix")
        args.append(prefix)
    if name:
        args.append("--name")
        args.append(name)
    conda_flags = os.environ.get("CONDA_FLAGS")
    if conda_flags:
        args.extend(shlex.split(conda_flags))

    logging.debug("$MAMBA_ROOT_PREFIX: %s", os.environ.get("MAMBA_ROOT_PREFIX"))

    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        universal_newlines=True,
    ) as p:
        if p.stdout:
            for line in _process_stdout(p.stdout):
                logging.info(line)

        if p.stderr:
            for line in p.stderr:
                logging.error(line.rstrip())

    if p.returncode != 0:
        print(
            f"Could not perform conda install using {file} lock file into {name or prefix}"
        )
        sys.exit(1)


def search_for_md5s(
    conda: PathLike, package_specs: List[dict], platform: str, channels: Sequence[str]
):
    """Use conda-search to determine the md5 metadata that we need.

    This is only needed if pkgs_dirs is set in condarc.
    Sadly this is going to be slow since we need to fetch each result individually
    due to the cli of conda search

    """

    def matchspec(spec):
        return (
            f"{spec['name']}["
            f"version={spec['version']},"
            f"subdir={spec['platform']},"
            f"channel={spec['channel']},"
            f"build={spec['build_string']}"
            "]"
        )

    found: Set[str] = set()
    logging.debug("Searching for package specs: \n%s", package_specs)
    packages: List[Tuple[str, str]] = [
        *[(d["name"], matchspec(d)) for d in package_specs],
        *[(d["name"], f"{d['name']}[url='{d['url_conda']}']") for d in package_specs],
        *[(d["name"], f"{d['name']}[url='{d['url']}']") for d in package_specs],
    ]

    for name, spec in packages:
        if name in found:
            continue
        channel_args = []
        for c in channels:
            channel_args += ["-c", c]
        cmd = [str(conda), "search", *channel_args, "--json", spec]
        logging.debug("seaching: %s", cmd)
        out = subprocess.run(
            cmd,
            encoding="utf8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=conda_env_override(platform),
        )
        content = json.loads(out.stdout)
        logging.debug("search output for %s\n%s", spec, content)
        if name in content:
            assert len(content[name]) == 1
            logging.debug("Found %s", name)
            yield content[name][0]
            found.add(name)


def fn_to_dist_name(fn: str) -> str:
    if fn.endswith(".conda"):
        fn, _, _ = fn.partition(".conda")
    elif fn.endswith(".tar.bz2"):
        fn, _, _ = fn.partition(".tar.bz2")
    else:
        raise RuntimeError(f"unexpected file type {fn}", fn)
    return fn


def make_lock_specs(
    *,
    platforms: List[str],
    src_files: List[pathlib.Path],
    include_dev_dependencies: bool = True,
    channel_overrides: Optional[Sequence[str]] = None,
) -> Dict[str, LockSpecification]:
    """Generate the lockfile specs from a set of input src_files"""
    res = {}
    for plat in platforms:
        lock_specs = parse_source_files(
            src_files=src_files,
            platform=plat,
            include_dev_dependencies=include_dev_dependencies,
        )

        lock_spec = aggregate_lock_specs(lock_specs)
        if channel_overrides:
            channels = list(channel_overrides)
        else:
            channels = lock_spec.channels
        lock_spec.channels = channels
        res[sys.platform] = lock_spec
    return res


def make_lock_files(
    conda: PathLike,
    platforms: List[str],
    kinds: List[str],
    src_files: List[pathlib.Path],
    include_dev_dependencies: bool = True,
    channel_overrides: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
    check_spec_hash: bool = False,
):
    """Generate the lock files for the given platforms from the src file provided

    Parameters
    ----------
    conda :
        The path to a conda or mamba executable
    platforms :
        List of platforms to generate the lock for
    src_files :
        Paths to a supported source file types
    include_dev_dependencies :
        For source types that separate out dev dependencies from regular ones,include those, default True
    channel_overrides :
        Forced list of channels to use.
    filename_template :
        Format for the lock file names. Must include {platform}.
    check_spec_hash :
        Validate that the existing spec hash has not already been generated for.

    """
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

    lock_specs = make_lock_specs(
        platforms=platforms,
        src_files=src_files,
        include_dev_dependencies=include_dev_dependencies,
        channel_overrides=channel_overrides,
    )

    for plat, lock_spec in lock_specs.items():
        for kind in kinds:
            if filename_template:
                context = {
                    "platform": lock_spec.platform,
                    "dev-dependencies": str(include_dev_dependencies).lower(),
                    # legacy key
                    "spec-hash": lock_spec.input_hash(),
                    "input-hash": lock_spec.input_hash(),
                    "version": pkg_resources.get_distribution("conda_lock").version,
                    "timestamp": datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
                }

                filename = filename_template.format(**context)
            else:
                filename = f"conda-{lock_spec.platform}.lock"

            lockfile = pathlib.Path(filename)
            if lockfile.exists() and check_spec_hash:
                existing_spec_hash = extract_input_hash(lockfile.read_text())
                if existing_spec_hash == lock_spec.input_hash():
                    print(
                        f"Spec hash already locked for {plat}. Skipping",
                        file=sys.stderr,
                    )
                    continue

            print(f"Generating lockfile(s) for {plat}...", file=sys.stderr)
            lockfile_contents = create_lockfile_from_spec(
                channels=lock_spec.channels,
                conda=conda,
                spec=lock_spec,
                kind=kind,
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


def is_micromamba(conda: PathLike) -> bool:
    return str(conda).endswith("micromamba") or str(conda).endswith("micromamba.exe")


def create_lockfile_from_spec(
    *,
    channels: Sequence[str],
    conda: PathLike,
    spec: LockSpecification,
    kind: str,
) -> List[str]:
    dry_run_install = solve_specs_for_arch(
        conda=conda,
        platform=spec.platform,
        channels=channels,
        specs=spec.specs,
    )
    logging.debug("dry_run_install:\n%s", dry_run_install)

    lockfile_contents = [
        "# Generated by conda-lock.",
        f"# platform: {spec.platform}",
        f"# input_hash: {spec.input_hash()}\n",
    ]

    if kind == "env":
        link_actions = dry_run_install["actions"]["LINK"]
        lockfile_contents.extend(
            [
                "channels:",
                *(f"  - {channel}" for channel in channels),
                "dependencies:",
                *(
                    f'  - {pkg["name"]}={pkg["version"]}={pkg["build_string"]}'
                    for pkg in link_actions
                ),
            ]
        )
    elif kind == "explicit":
        lockfile_contents.append("@EXPLICIT\n")

        link_actions = dry_run_install["actions"]["LINK"]
        for link in link_actions:
            if is_micromamba(conda):
                link["url_base"] = fn_to_dist_name(link["url"])
                link["dist_name"] = fn_to_dist_name(link["fn"])
            else:
                link[
                    "url_base"
                ] = f"{link['base_url']}/{link['platform']}/{link['dist_name']}"

            link["url"] = f"{link['url_base']}.tar.bz2"
            link["url_conda"] = f"{link['url_base']}.conda"
        link_dists = {link["dist_name"] for link in link_actions}

        fetch_actions = dry_run_install["actions"]["FETCH"]

        fetch_by_dist_name = {fn_to_dist_name(pkg["fn"]): pkg for pkg in fetch_actions}

        non_fetch_packages = link_dists - set(fetch_by_dist_name)
        if len(non_fetch_packages) > 0:
            for search_res in search_for_md5s(
                conda=conda,
                package_specs=[
                    x for x in link_actions if x["dist_name"] in non_fetch_packages
                ],
                platform=spec.platform,
                channels=channels,
            ):
                dist_name = fn_to_dist_name(search_res["fn"])
                fetch_by_dist_name[dist_name] = search_res

        for pkg in link_actions:
            dist_name = (
                fn_to_dist_name(pkg["fn"]) if is_micromamba(conda) else pkg["dist_name"]
            )
            url = fetch_by_dist_name[dist_name]["url"]
            md5 = fetch_by_dist_name[dist_name]["md5"]
            lockfile_contents.append(f"{url}#{md5}")

        def sanitize_lockfile_line(line):
            line = line.strip()
            if line == "":
                return "#"
            else:
                return line

        lockfile_contents = [sanitize_lockfile_line(line) for line in lockfile_contents]
    else:
        raise ValueError(f"Unrecognised lock kind {kind}.")

    logging.debug("lockfile_contents:\n%s\n", lockfile_contents)
    return lockfile_contents


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
    src_files: List[pathlib.Path], platform: str, include_dev_dependencies: bool
) -> List[LockSpecification]:
    desired_envs = []
    for src_file in src_files:
        if src_file.name == "meta.yaml":
            desired_envs.append(
                parse_meta_yaml_file(src_file, platform, include_dev_dependencies)
            )
        elif src_file.name == "pyproject.toml":
            desired_envs.append(
                parse_pyproject_toml(src_file, platform, include_dev_dependencies)
            )
        else:
            desired_envs.append(parse_environment_file(src_file, platform))
    return desired_envs


def aggregate_lock_specs(lock_specs: List[LockSpecification]) -> LockSpecification:
    # union the dependencies
    specs = list(
        set(chain.from_iterable([lock_spec.specs for lock_spec in lock_specs]))
    )

    # pick the first non-empty channel
    channels: List[str] = next(
        (lock_spec.channels for lock_spec in lock_specs if lock_spec.channels), []
    )

    # pick the first non-empty platform
    platform = next(
        (lock_spec.platform for lock_spec in lock_specs if lock_spec.platform), ""
    )

    return LockSpecification(specs=specs, channels=channels, platform=platform)


def _ensureconda(
    mamba: bool = False,
    micromamba: bool = False,
    conda: bool = False,
    conda_exe: bool = False,
):
    _conda_exe = ensureconda.ensureconda(
        mamba=mamba,
        micromamba=micromamba,
        conda=conda,
        conda_exe=conda_exe,
    )

    return _conda_exe


def _determine_conda_executable(
    conda_executable: Optional[str], mamba: bool, micromamba: bool
):
    if conda_executable:
        if pathlib.Path(conda_executable).exists():
            yield conda_executable
        yield shutil.which(conda_executable)

    yield _ensureconda(mamba=mamba, micromamba=micromamba, conda=True, conda_exe=True)


def determine_conda_executable(
    conda_executable: Optional[str], mamba: bool, micromamba: bool
):
    for candidate in _determine_conda_executable(conda_executable, mamba, micromamba):
        if candidate is not None:
            if is_micromamba(candidate) and "MAMBA_ROOT_PREFIX" not in os.environ:
                mamba_root_prefix = pathlib.Path(candidate).parent / "mamba_root"
                mamba_root_prefix.mkdir(exist_ok=True, parents=True)
                os.environ["MAMBA_ROOT_PREFIX"] = str(mamba_root_prefix)

            return candidate
    raise RuntimeError("Could not find conda (or compatible) executable")


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
        platforms=platforms or DEFAULT_PLATFORMS,
        include_dev_dependencies=include_dev_dependencies,
        channel_overrides=channel_overrides,
        filename_template=filename_template,
        kinds=kinds or DEFAULT_KINDS,
        check_spec_hash=check_input_hash,
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
    "--strip-auth",
    is_flag=True,
    default=False,
    help="Strip the basic auth credentials from the lockfile.",
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
# @click.option(
#     "-m",
#     "--mode",
#     type=click.Choice(["default", "docker"], case_sensitive=True),
#     default="default",
#     help="""
#             Run this conda-lock in an isolated docker container.  This may be
#             required to account for some issues where conda-lock conflicts with
#             existing condarc configurations.""",
# )
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
    check_input_hash: bool,
    log_level,
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
    files = [pathlib.Path(file) for file in files]
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
):
    """Perform a conda install"""
    logging.basicConfig(level=log_level)
    auth = json.loads(auth) if auth else read_json(auth_file) if auth_file else None
    _conda_exe = determine_conda_executable(conda, mamba=mamba, micromamba=micromamba)
    install_func = partial(do_conda_install, conda=_conda_exe, prefix=prefix, name=name)
    if validate_platform:
        lockfile = read_file(lock_file)
        try:
            do_validate_platform(lockfile)
        except PlatformValidationError as error:
            raise PlatformValidationError(
                error.args[0] + " Disable validation with `--validate-platform=False`."
            )
    if auth:
        lockfile = read_file(lock_file)
        with _add_auth(lockfile, auth) as lockfile_with_auth:
            install_func(file=lockfile_with_auth)
    else:
        install_func(file=lock_file)


if __name__ == "__main__":
    main()
