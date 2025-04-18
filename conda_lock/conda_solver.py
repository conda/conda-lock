import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from contextlib import contextmanager
from textwrap import dedent
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    MutableSequence,
    Optional,
    Sequence,
)
from urllib.parse import urlsplit, urlunsplit

from typing_extensions import TypedDict

from conda_lock.interfaces.vendored_conda import MatchSpec
from conda_lock.invoke_conda import (
    PathLike,
    _get_conda_flags,
    conda_env_override,
    conda_pkgs_dir,
    is_micromamba,
)
from conda_lock.lockfile import apply_categories
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.models.channel import Channel, normalize_url_with_placeholders
from conda_lock.models.lock_spec import Dependency, VersionedDependency


logger = logging.getLogger(__name__)


class FetchAction(TypedDict):
    """
    FETCH actions include all the entries from the corresponding package's
    repodata.json
    """

    channel: str
    constrains: Optional[List[str]]
    depends: Optional[List[str]]
    fn: str
    md5: str
    sha256: Optional[str]
    name: str
    subdir: str
    timestamp: int
    url: str
    version: str


class LinkAction(TypedDict):
    """
    LINK actions include only entries from conda-meta, notably missing
    dependency and constraint information
    """

    base_url: str
    channel: str
    dist_name: str
    name: str
    platform: str
    version: str


class InstallActions(TypedDict):
    LINK: List[LinkAction]
    FETCH: List[FetchAction]


class DryRunInstall(TypedDict):
    actions: InstallActions


def _to_match_spec(
    conda_dep_name: str,
    conda_version: Optional[str],
    build: Optional[str],
    conda_channel: Optional[str],
) -> str:
    kwargs = dict(name=conda_dep_name)
    if conda_version:
        kwargs["version"] = conda_version
    if build:
        kwargs["build"] = build
        if "version" not in kwargs:
            kwargs["version"] = "*"
    if conda_channel:
        kwargs["channel"] = conda_channel

    ms = MatchSpec(**kwargs)
    # Since MatchSpec doesn't round trip to the cli well
    if conda_channel:
        # this will return "channel_name::package_name"
        return str(ms)
    else:
        # this will return only "package_name" even if there's a channel in the kwargs
        return ms.conda_build_form()


def extract_json_object(proc_stdout: str) -> str:
    try:
        return proc_stdout[proc_stdout.index("{") : proc_stdout.rindex("}") + 1]
    except ValueError:
        return proc_stdout


def solve_conda(
    conda: PathLike,
    specs: Dict[str, Dependency],
    locked: Dict[str, LockedDependency],
    update: List[str],
    platform: str,
    channels: List[Channel],
    mapping_url: str,
) -> Dict[str, LockedDependency]:
    """
    Solve (or update a previous solution of) conda specs for the given platform

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    specs :
        Conda package specifications
    locked :
        Previous solution for the given platform (conda packages only)
    update :
        Named of packages to update to the latest version compatible with specs
    platform :
        Target platform
    channels :
        Channels to query

    """

    conda_specs = [
        _to_match_spec(dep.name, dep.version, dep.build, dep.conda_channel)
        for dep in specs.values()
        if isinstance(dep, VersionedDependency) and dep.manager == "conda"
    ]
    conda_locked = {dep.name: dep for dep in locked.values() if dep.manager == "conda"}
    to_update = set(update).intersection(conda_locked)

    if to_update:
        dry_run_install = update_specs_for_arch(
            conda=conda,
            platform=platform,
            channels=channels,
            specs=conda_specs,
            locked=conda_locked,
            update=list(to_update),
        )
    else:
        dry_run_install = solve_specs_for_arch(
            conda=conda,
            platform=platform,
            channels=channels,
            specs=conda_specs,
        )
    logging.debug("dry_run_install:\n%s", dry_run_install)

    # extract dependencies from package plan
    planned = {}
    for action in dry_run_install["actions"]["FETCH"]:
        dependencies = {}
        for dep in action.get("depends") or []:
            matchspec = MatchSpec(dep)
            name = matchspec.name
            version = (
                matchspec.version.spec_str if matchspec.version is not None else ""
            )
            dependencies[name] = version

        locked_dependency = LockedDependency(
            name=action["name"],
            version=action["version"],
            manager="conda",
            platform=platform,
            dependencies=dependencies,
            # TODO: Normalize URL here and inject env vars
            url=normalize_url_with_placeholders(action["url"], channels=channels),
            # NB: virtual packages may have no hash
            hash=HashModel(
                md5=action["md5"] if "md5" in action else "",
                sha256=action.get("sha256"),
            ),
        )
        planned[action["name"]] = locked_dependency

    # propagate categories from explicit to transitive dependencies
    apply_categories(
        requested={k: v for k, v in specs.items() if v.manager == "conda"},
        planned=planned,
        mapping_url=mapping_url,
    )

    return planned


def _get_repodata_record(
    pkgs_dirs: List[pathlib.Path], dist_name: str
) -> Optional[FetchAction]:
    """Get the repodata_record.json of a given distribution from the package cache.

    On rare occasion during the CI tests, conda fails to find a package in the
    package cache, perhaps because the package is still being processed? Waiting for
    0.1 seconds seems to solve the issue. Here we allow for a full second to elapse
    before giving up.
    """
    NUM_RETRIES = 10
    for retry in range(1, NUM_RETRIES + 1):
        for pkgs_dir in pkgs_dirs:
            record = pkgs_dir / dist_name / "info" / "repodata_record.json"
            if record.exists():
                with open(record) as f:
                    repodata: FetchAction = json.load(f)
                return repodata
        logger.warning(
            f"Failed to find repodata_record.json for {dist_name}. "
            f"Retrying in 0.1 seconds ({retry}/{NUM_RETRIES})"
        )
        time.sleep(0.1)
    logger.warning(f"Failed to find repodata_record.json for {dist_name}. Giving up.")
    return None


def _get_pkgs_dirs(
    *,
    conda: PathLike,
    platform: str,
    method: Optional[Literal["config", "info"]] = None,
) -> List[pathlib.Path]:
    """Extract the package cache directories from the conda configuration."""
    if method is None:
        method = "config" if is_micromamba(conda) else "info"
    if method == "config":
        # 'package cache' was added to 'micromamba info' in v1.4.6.
        args = [str(conda), "config", "--json", "list", "pkgs_dirs"]
    elif method == "info":
        args = [str(conda), "info", "--json"]
    env = conda_env_override(platform)
    output = subprocess.check_output(args, env=env).decode()
    json_object_str = extract_json_object(output)
    json_object: Dict[str, Any] = json.loads(json_object_str)
    pkgs_dirs_list: List[str]
    if "pkgs_dirs" in json_object:
        pkgs_dirs_list = json_object["pkgs_dirs"]
    elif "package cache" in json_object:
        pkgs_dirs_list = json_object["package cache"]
    else:
        raise ValueError(
            f"Unable to extract pkgs_dirs from {json_object}. "
            "Please report this issue to the conda-lock developers."
        )
    pkgs_dirs = [pathlib.Path(d) for d in pkgs_dirs_list]
    return pkgs_dirs


def _reconstruct_fetch_actions(
    conda: PathLike, platform: str, dry_run_install: DryRunInstall
) -> DryRunInstall:
    """
    Conda may choose to link a previously downloaded distribution from pkgs_dirs rather
    than downloading a fresh one. Find the repodata record in existing distributions
    that have only a LINK action, and use it to synthesize a corresponding FETCH action
    with the metadata we need to extract for the package plan.
    """
    if "LINK" not in dry_run_install["actions"]:
        dry_run_install["actions"]["LINK"] = []
    if "FETCH" not in dry_run_install["actions"]:
        dry_run_install["actions"]["FETCH"] = []

    link_actions = {p["name"]: p for p in dry_run_install["actions"]["LINK"]}
    fetch_actions = {p["name"]: p for p in dry_run_install["actions"]["FETCH"]}
    link_only_names = set(link_actions.keys()).difference(fetch_actions.keys())
    if link_only_names:
        pkgs_dirs = _get_pkgs_dirs(conda=conda, platform=platform)
    else:
        pkgs_dirs = []

    for link_pkg_name in link_only_names:
        link_action = link_actions[link_pkg_name]
        if "dist_name" in link_action:
            dist_name = link_action["dist_name"]
        elif "fn" in link_action:
            dist_name = str(link_action["fn"])
            if dist_name.endswith(".tar.bz2"):
                dist_name = dist_name[:-8]
            elif dist_name.endswith(".conda"):
                dist_name = dist_name[:-6]
            else:
                raise ValueError(f"Unknown filename format: {dist_name}")
        else:
            raise ValueError(f"Unable to extract the dist_name from {link_action}.")
        repodata = _get_repodata_record(pkgs_dirs, dist_name)
        if repodata is None:
            raise FileNotFoundError(
                f"Distribution '{dist_name}' not found in pkgs_dirs {pkgs_dirs}"
            )
        dry_run_install["actions"]["FETCH"].append(repodata)
    return dry_run_install


def solve_specs_for_arch(
    conda: PathLike,
    channels: Sequence[Channel],
    specs: List[str],
    platform: str,
) -> DryRunInstall:
    """
    Solve conda specifications for the given platform

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    channels :
        Channels to query
    specs :
        Conda package specifications
    platform :
        Target platform

    """
    args: MutableSequence[str] = [
        str(conda),
        "create",
        "--prefix",
        os.path.join(conda_pkgs_dir(), "prefix"),
        "--dry-run",
        "--json",
    ]
    args.extend(_get_conda_flags(channels=channels, platform=platform))
    args.extend(specs)
    logger.info("%s using specs %s", platform, specs)
    logger.debug(f"Running command {shlex.join(args)}")
    proc = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
        [str(arg) for arg in args],
        env=conda_env_override(platform),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    def print_proc(proc: subprocess.CompletedProcess) -> None:
        print(f"    Command: {proc.args}", file=sys.stderr)
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}", file=sys.stderr)
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}", file=sys.stderr)

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        try:
            err_json = json.loads(proc.stdout)
            try:
                message = err_json["message"]
            except KeyError:
                print("Message key not found in json! returning the full json text")
                message = err_json
        except json.JSONDecodeError as e:
            print(f"Failed to parse json, {e}", file=sys.stderr)
            message = proc.stdout

        print(
            f"Could not lock the environment for platform {platform}", file=sys.stderr
        )
        if message:
            print(message, file=sys.stderr)
        print_proc(proc)

        raise

    try:
        dryrun_install: DryRunInstall = json.loads(extract_json_object(proc.stdout))
        return _reconstruct_fetch_actions(conda, platform, dryrun_install)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse json: '{proc.stdout}'") from e


def _get_installed_conda_packages(
    conda: PathLike,
    platform: str,
    prefix: str,
) -> Dict[str, LinkAction]:
    """
    Get the installed conda packages for the given prefix.

    Try to get installed packages, first with --no-pip flag, then without if that fails.
    The --no-pip flag was added in Conda v2.1.0 (2013), but for mamba/micromamba only in
    v2.0.7 (March 2025).
    """
    try:
        output = subprocess.check_output(
            [str(conda), "list", "--no-pip", "-p", prefix, "--json"],
            env=conda_env_override(platform),
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        err_output = (
            e.output.decode("utf-8") if isinstance(e.output, bytes) else e.output
        )
        if "The following argument was not expected: --no-pip" in err_output:
            logger.warning(
                f"The '--no-pip' flag is not supported by {conda}. Please consider upgrading."
            )
            # Retry without --no-pip
            output = subprocess.check_output(
                [str(conda), "list", "-p", prefix, "--json"],
                env=conda_env_override(platform),
                stderr=subprocess.STDOUT,
            )
        else:
            # Re-raise if it's a different error.
            raise
    decoded_output = output.decode("utf-8")
    installed: Dict[str, LinkAction] = {
        entry["name"]: entry for entry in json.loads(decoded_output)
    }
    return installed


def update_specs_for_arch(
    conda: PathLike,
    specs: List[str],
    locked: Dict[str, LockedDependency],
    update: List[str],
    platform: str,
    channels: Sequence[Channel],
) -> DryRunInstall:
    """
    Update a previous solution for the given platform

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    specs :
        Conda package specifications
    locked :
        Previous solution for the given platform (conda packages only)
    update :
        Named of packages to update to the latest version compatible with specs
    platform :
        Target platform
    channels :
        Channels to query

    """

    with fake_conda_environment(locked.values(), platform=platform) as prefix:
        installed = _get_installed_conda_packages(conda, platform, prefix)
        spec_for_name = {MatchSpec(v).name: v for v in specs}  # pyright: ignore
        to_update = [
            spec_for_name[name] for name in set(installed).intersection(update)
        ]
        if to_update:
            # NB: [micro]mamba and mainline conda have different semantics for `install` and `update`
            # - conda:
            #   * update -> apply all nonmajor updates unconditionally (unless pinned)
            #   * install -> install or update target to latest version compatible with constraint
            # - micromamba:
            #   * update -> update target to latest version compatible with constraint
            #   * install -> update target if current version incompatible with constraint, otherwise _do nothing_
            # - mamba:
            #   * update -> apply all nonmajor updates unconditionally (unless pinned)
            #   * install -> update target if current version incompatible with constraint, otherwise _do nothing_
            # Our `update` should always update the target to the latest version compatible with the constraint,
            # while updating as few other packages as possible. With mamba this can only be done with pinning.
            if pathlib.Path(conda).name.startswith("mamba"):
                # pin non-updated packages to prevent _any_ movement
                pinned_filename = pathlib.Path(prefix) / "conda-meta" / "pinned"
                assert not pinned_filename.exists()
                with open(pinned_filename, "w") as pinned:
                    for name in set(installed.keys()).difference(update):
                        pinned.write(f"{name} =={installed[name]['version']}\n")
                args = [
                    str(conda),
                    "update",
                    *_get_conda_flags(channels=channels, platform=platform),
                ]
                print(
                    "Warning: mamba cannot update single packages without resorting to pinning. "
                    "If the update fails to solve, try with conda or micromamba instead.",
                    file=sys.stderr,
                )
            else:
                args = [
                    str(conda),
                    "update" if is_micromamba(conda) else "install",
                    *_get_conda_flags(channels=channels, platform=platform),
                ]
            cmd = [
                str(arg)
                for arg in [*args, "-p", prefix, "--json", "--dry-run", *to_update]
            ]
            logger.debug(f"Running command {shlex.join(cmd)}")
            proc = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
                cmd,
                env=conda_env_override(platform),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf8",
            )

            try:
                proc.check_returncode()
            except subprocess.CalledProcessError as exc:
                err_json = json.loads(proc.stdout)
                raise RuntimeError(
                    f"Could not lock the environment for platform {platform}: {err_json.get('message')}"
                ) from exc

            dryrun_install: DryRunInstall = json.loads(extract_json_object(proc.stdout))
        else:
            dryrun_install = {"actions": {"LINK": [], "FETCH": []}}

        if "actions" not in dryrun_install:
            dryrun_install["actions"] = {"LINK": [], "FETCH": []}

        updated = {entry["name"]: entry for entry in dryrun_install["actions"]["LINK"]}
        for package in set(installed).difference(updated):
            entry = installed[package]
            fn = f"{entry['dist_name']}.tar.bz2"
            channel = f"{entry['base_url']}/{entry['platform']}"
            url = f"{channel}/{fn}"
            md5 = locked[package].hash.md5
            if md5 is None:
                raise RuntimeError("Conda packages require non-null md5 hashes")
            sha256 = locked[package].hash.sha256
            dryrun_install["actions"]["FETCH"].append(
                {
                    "name": entry["name"],
                    "channel": channel,
                    "url": url,
                    "fn": fn,
                    "md5": md5,
                    "sha256": sha256,
                    "version": entry["version"],
                    "depends": [
                        f"{k} {v}".strip()
                        for k, v in locked[entry["name"]].dependencies.items()
                    ],
                    "constrains": [],
                    "subdir": entry["platform"],
                    "timestamp": 0,
                }
            )
            dryrun_install["actions"]["LINK"].append(entry)
        return _reconstruct_fetch_actions(conda, platform, dryrun_install)


@contextmanager
def fake_conda_environment(
    locked: Iterable[LockedDependency], platform: str
) -> Iterator[str]:
    """
    Create a fake conda prefix containing metadata corresponding to the provided dependencies

    Parameters
    ----------
    locked :
        Previous solution
    platform :
        Target platform

    """
    with tempfile.TemporaryDirectory() as prefix:
        conda_meta = pathlib.Path(prefix) / "conda-meta"
        conda_meta.mkdir()
        (conda_meta / "history").touch()
        for dep in (
            dep for dep in locked if dep.manager == "conda" and dep.platform == platform
        ):
            url = urlsplit(dep.url)
            path = pathlib.PurePosixPath(url.path)
            channel = urlunsplit(
                (url.scheme, url.hostname, str(path.parent), None, None)
            )
            truncated_path = path
            while truncated_path.suffix in {".tar", ".bz2", ".gz", ".conda"}:
                truncated_path = truncated_path.with_suffix("")
            build = truncated_path.name.split("-")[-1]
            try:
                build_number = int(build.split("_")[-1])
            except ValueError:
                build_number = 0
            entry = {
                "name": dep.name,
                "channel": channel,
                "url": dep.url,
                "md5": dep.hash.md5,
                "build": build,
                "build_number": build_number,
                "version": dep.version,
                "subdir": path.parent.name,
                "fn": path.name,
                "depends": [f"{k} {v}".strip() for k, v in dep.dependencies.items()],
            }
            # mamba requires these to be stringlike so null are not allowed here
            if dep.hash.sha256 is not None:
                entry["sha256"] = dep.hash.sha256

            with open(conda_meta / (truncated_path.name + ".json"), "w") as f:
                json.dump(entry, f, indent=2)
            make_fake_python_binary(prefix)
        yield prefix


def make_fake_python_binary(prefix: str) -> None:
    """Create a fake python binary in the given prefix.

    Our fake Conda environment contains metadata indicating that `python`
    is installed in the prefix, however no packages are installed.

    This is intended to prevent failure of `PrefixData.load_site_packages`
    which was introduced in libmamba v2. That function invokes the command
    `python -q -m pip inspect --local` to check for installed pip packages,
    where the `python` binary is the one in the conda prefix. Our fake
    prefix only contains the package metadata records, not the actual
    packages or binaries. At this stage for conda-lock, we are only
    interested in the conda packages, so we spoof the `python` binary
    to return an empty stdout so that things proceed without error.
    """
    # Write the fake Python script to a file
    fake_python_script = pathlib.Path(prefix) / "fake_python_script.py"
    fake_python_script.write_text(
        dedent(
            """\
            import sys
            import shlex

            cmd = shlex.join(sys.argv)

            stderr_message = f'''\
            This is a fake python binary generated by conda-lock.

            It prevents libmamba from failing when it tries to check for installed \
            pip packages.

            For more details, see the docstring for `make_fake_python_binary`.

            This was called as:
                {cmd}
            '''

            print(stderr_message, file=sys.stderr, flush=True, end='')

            if "-m pip" in cmd:
                # Simulate an empty `pip inspect` output
                print('{}', flush=True)
            else:
                raise RuntimeError("Expected to invoke pip module with `-m pip`.")
            """
        )
    )

    if sys.platform == "win32":
        # On Windows, copy sys.executable to prefix/Scripts/python.exe
        fake_python_binary = pathlib.Path(prefix) / "Scripts" / "python.exe"
        fake_python_binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sys.executable, fake_python_binary)

        # Adjust the environment to ensure our fake script is executed
        # Create a wrapper batch file that sets PYTHONPATH
        wrapper_batch = pathlib.Path(prefix) / "Scripts" / "python.bat"
        wrapper_batch_content = dedent(f"""\
            @echo off
            set PYTHONPATH={prefix};%PYTHONPATH%
            "{fake_python_binary}" %*
        """)
        wrapper_batch.write_text(wrapper_batch_content)
    else:
        # On Unix-like systems, create a shell script that calls the script
        fake_python_binary = pathlib.Path(prefix) / "bin" / "python"
        fake_python_binary.parent.mkdir(parents=True, exist_ok=True)
        shell_script_content = dedent(f"""\
            #!/usr/bin/env sh
            "{sys.executable}" "{fake_python_script}" "$@"
        """)
        fake_python_binary.write_text(shell_script_content)
        fake_python_binary.chmod(0o755)
