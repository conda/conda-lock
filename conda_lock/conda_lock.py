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
from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.meta_yaml import parse_meta_yaml_file
from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml


PathLike = Union[str, pathlib.Path]

# Captures basic auth credentials, if they exists, in the second capture group.
AUTH_PATTERN = re.compile(r"^(https?:\/\/)(.*:.*@)?(.*)")

# Captures the domain in the second group.
DOMAIN_PATTERN = re.compile(r"^(https?:\/\/)?([^\/]+)(.*)")

if not (sys.version_info.major >= 3 and sys.version_info.minor >= 6):
    print("conda_lock needs to run under python >=3.6")
    sys.exit(1)


CONDA_PKGS_DIRS = None
DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]


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


def do_conda_install(conda: PathLike, prefix: str, name: str, file: str) -> None:

    if prefix and name:
        raise ValueError("Provide either prefix, or name, but not both.")

    args: MutableSequence[PathLike] = [
        str(conda),
        "create",
        "--file",
        file,
        "--yes",
        "--json",
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
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )
    logging.debug("install process: %s", proc)

    def print_proc(proc):
        print(f"    Command: {proc.args}")
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}")
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}")

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        print(
            f"Could not perform conda install using {file} lock file into {name or prefix}"
        )
        print(_handle_subprocess_stdout(proc.stdout))
        print_proc(proc)
        sys.exit(1)


def _handle_subprocess_stdout(stdout):
    try:
        err_json = json.loads(stdout.split("\x00")[-1])
        if err_json.get("exception_name") == "CondaMultiError":
            return "\n\n".join(error["message"] for error in err_json["errors"])
        return err_json["message"]
    except json.JSONDecodeError as e:
        return f"Failed to parse json, {e}"


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


def make_lock_files(
    conda: PathLike,
    platforms: List[str],
    src_files: List[pathlib.Path],
    include_dev_dependencies: bool = True,
    channel_overrides: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
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

    """
    if filename_template:
        if "{platform}" not in filename_template and len(platforms) > 1:
            print(
                "{platform} must be in filename template when locking"
                f" more than one platform: {', '.join(platforms)}",
                file=sys.stderr,
            )
            sys.exit(1)

    for plat in platforms:
        print(f"generating lockfile for {plat}", file=sys.stderr)
        lock_specs = parse_source_files(
            src_files=src_files,
            platform=plat,
            include_dev_dependencies=include_dev_dependencies,
        )

        lock_spec = aggregate_lock_specs(lock_specs)
        if channel_overrides:
            channels = channel_overrides
        else:
            channels = lock_spec.channels

        lockfile_contents = create_lockfile_from_spec(
            channels=channels, conda=conda, spec=lock_spec
        )

        def sanitize_lockfile_line(line):
            line = line.strip()
            if line == "":
                return "#"
            else:
                return line

        if filename_template:
            context = {
                "platform": lock_spec.platform,
                "dev-dependencies": str(include_dev_dependencies).lower(),
                "spec-hash": lock_spec.env_hash(),
                "version": pkg_resources.get_distribution("conda_lock").version,
                "timestamp": datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
            }

            filename = filename_template.format(**context)
        else:
            filename = f"conda-{lock_spec.platform}.lock"
        with open(filename, "w") as fo:
            fo.write(
                "\n".join(sanitize_lockfile_line(ln) for ln in lockfile_contents) + "\n"
            )

    print("To use the generated lock files create a new environment:", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "     conda create --name YOURENV --file conda-linux-64.lock", file=sys.stderr
    )
    print("", file=sys.stderr)


def is_micromamba(conda: PathLike) -> bool:
    return str(conda).endswith("micromamba") or str(conda).endswith("micromamba.exe")


def create_lockfile_from_spec(
    *, channels: Sequence[str], conda: PathLike, spec: LockSpecification
) -> List[str]:
    dry_run_install = solve_specs_for_arch(
        conda=conda,
        platform=spec.platform,
        channels=channels,
        specs=spec.specs,
    )
    logging.debug("dry_run_install:\n%s", dry_run_install)
    lockfile_contents = [
        f"# platform: {spec.platform}",
        f"# env_hash: {spec.env_hash()}\n",
        "@EXPLICIT\n",
    ]

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
    stripped_domains_doc = "\n".join(f"# - {domain}" for domain in stripped_domains)
    stripped_lockfile = "\n".join(stripped_lockfile_lines)
    return f"# The following domains require authentication:\n{stripped_domains_doc}\n{stripped_lockfile}\n"


def run_lock(
    environment_files: List[pathlib.Path],
    conda_exe: Optional[str],
    platforms: Optional[List[str]] = None,
    mamba: bool = False,
    micromamba: bool = False,
    include_dev_dependencies: bool = True,
    channel_overrides: Optional[Sequence[str]] = None,
    filename_template: Optional[str] = None,
) -> None:
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
    default=["environment.yml"],
    type=click.Path(),
    multiple=True,
    help="path to a conda environment specification(s)",
)
@click.option(
    "--filename-template",
    default="conda-{platform}.lock",
    help="Template for the lock file names. Must include {platform} token. For a full list and description of available tokens, see the command help text.",
)
@click.option(
    "--strip-auth",
    is_flag=True,
    default=False,
    help="Strip the basic auth credentials from the lockfile.",
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
    filename_template,
    strip_auth,
):
    """Generate fully reproducible lock files for conda environments.

    By default, the lock files are written to conda-{platform}.lock. These filenames can be customized using the
    --filename-template argument. The following tokens are available:

    \b
        platform: The platform this lock file was generated for (conda subdir).
        dev-dependencies: Whether or not dev dependencies are included in this lock file.
        spec-hash: A sha256 hash of the lock file spec.
        version: The version of conda-lock used to generate this lock file.
        timestamp: The approximate timestamp of the output file in ISO8601 basic format.
    """
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
        lock_func(filename_template=filename_template)


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
@click.option("--auth-file", help="Path to the authentication file.", default="")
@click.argument("lock-file")
def install(conda, mamba, micromamba, prefix, name, lock_file, auth_file):
    """Perform a conda install"""
    auth = read_json(auth_file) if auth_file else None
    _conda_exe = determine_conda_executable(conda, mamba=mamba, micromamba=micromamba)
    install_func = partial(do_conda_install, conda=_conda_exe, prefix=prefix, name=name)
    if auth:
        lockfile = read_file(lock_file)
        with _add_auth(lockfile, auth) as lockfile_with_auth:
            install_func(file=lockfile_with_auth)
    else:
        install_func(file=lock_file)


if __name__ == "__main__":
    main()
