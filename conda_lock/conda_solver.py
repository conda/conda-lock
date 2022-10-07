import json
import logging
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile

from contextlib import contextmanager
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    MutableSequence,
    Optional,
    Sequence,
    cast,
)
from urllib.parse import urlsplit, urlunsplit

from typing_extensions import TypedDict

from conda_lock._vendor.conda.models.match_spec import MatchSpec
from conda_lock.invoke_conda import (
    PathLike,
    _get_conda_flags,
    conda_env_override,
    conda_pkgs_dir,
    is_micromamba,
)
from conda_lock.models.channel import Channel
from conda_lock.src_parser import (
    Dependency,
    HashModel,
    LockedDependency,
    VersionedDependency,
    _apply_categories,
)


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

    def normalize_url(url: str) -> str:
        for channel in channels:
            candidate1 = channel.conda_token_replaced_url()
            if url.startswith(candidate1):
                url = url.replace(candidate1, channel.url, 1)

            candidate2 = channel.env_replaced_url()
            if url.startswith(candidate2):
                url = url.replace(candidate2, channel.url, 1)
        return url

    # extract dependencies from package plan
    planned = {
        action["name"]: LockedDependency(
            name=action["name"],
            version=action["version"],
            manager="conda",
            platform=platform,
            dependencies={
                item.split()[0]: " ".join(item.split(" ")[1:])
                for item in action.get("depends") or []
            },
            # TODO: Normalize URL here and inject env vars
            url=normalize_url(action["url"]),
            # NB: virtual packages may have no hash
            hash=HashModel(
                md5=action["md5"] if "md5" in action else "",
                sha256=action.get("sha256"),
            ),
        )
        for action in dry_run_install["actions"]["FETCH"]
    }

    # propagate categories from explicit to transitive dependencies
    _apply_categories(
        requested={k: v for k, v in specs.items() if v.manager == "conda"},
        planned=planned,
    )

    return planned


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
    # NB: micromamba does not support info --json, nor does it appear to honor pkgs_dirs from .condarc
    if not is_micromamba(conda):
        if link_only_names:
            pkgs_dirs = [
                pathlib.Path(d)
                for d in json.loads(
                    extract_json_object(
                        subprocess.check_output(
                            [str(conda), "info", "--json"],
                            env=conda_env_override(platform),
                        ).decode()
                    )
                )["pkgs_dirs"]
            ]
        else:
            pkgs_dirs = []

        for link_pkg_name in link_only_names:
            link_action = link_actions[link_pkg_name]
            for pkgs_dir in pkgs_dirs:
                record = (
                    pkgs_dir
                    / link_action["dist_name"]
                    / "info"
                    / "repodata_record.json"
                )
                if record.exists():
                    with open(record) as f:
                        repodata: FetchAction = json.load(f)
                    break
            else:
                raise FileExistsError(
                    f'Distribution \'{link_action["dist_name"]}\' not found in pkgs_dirs {pkgs_dirs}'
                )
            dry_run_install["actions"]["FETCH"].append(repodata)
    else:
        # NB: micromamba LINK actions contain the same metadata as FETCH
        # actions, and so can be used to fill out the FETCH section.
        # Explicitly copy key-by-key to make missing keys obvious, should
        # this change in the future.
        for link_pkg_name in link_only_names:
            item = cast(Dict[str, Any], link_actions[link_pkg_name])
            repodata = {
                "channel": item["channel"],
                "constrains": item.get("constrains"),
                "depends": item.get("depends"),
                "fn": item["fn"],
                "md5": item["md5"],
                "name": item["name"],
                "subdir": item["subdir"],
                "timestamp": item["timestamp"],
                "url": item["url"],
                "version": item["version"],
                "sha256": item.get("sha256"),
            }

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
    conda_flags = os.environ.get("CONDA_FLAGS")
    if conda_flags:
        args.extend(shlex.split(conda_flags))
    if channels:
        args.append("--override-channels")

    for channel in channels:
        args.extend(["--channel", channel.env_replaced_url()])
        if channel == "defaults" and platform in {"win-64", "win-32"}:
            # msys2 is a windows-only channel that conda automatically
            # injects if the host platform is Windows. If our host
            # platform is not Windows, we need to add it manually
            args.extend(["--channel", "msys2"])
    args.extend(specs)
    logger.info("%s using specs %s", platform, specs)
    proc = subprocess.run(
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
    except json.JSONDecodeError:
        raise


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
        installed: Dict[str, LinkAction] = {
            entry["name"]: entry
            for entry in json.loads(
                subprocess.check_output(
                    [str(conda), "list", "-p", prefix, "--json"],
                    env=conda_env_override(platform),
                )
            )
        }
        spec_for_name = {MatchSpec(v).name: v for v in specs}
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
                        pinned.write(f'{name} =={installed[name]["version"]}\n')
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
            proc = subprocess.run(
                [
                    str(arg)
                    for arg in args + ["-p", prefix, "--json", "--dry-run", *to_update]
                ],
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

            dryrun_install: DryRunInstall = json.loads(proc.stdout)
        else:
            dryrun_install = {"actions": {"LINK": [], "FETCH": []}}

        if "actions" not in dryrun_install:
            dryrun_install["actions"] = {"LINK": [], "FETCH": []}

        updated = {entry["name"]: entry for entry in dryrun_install["actions"]["LINK"]}
        for package in set(installed).difference(updated):
            entry = installed[package]
            fn = f'{entry["dist_name"]}.tar.bz2'
            if is_micromamba(conda):
                channel = f'{entry["base_url"]}'
            else:
                channel = f'{entry["base_url"]}/{entry["platform"]}'
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
            while path.suffix in {".tar", ".bz2", ".gz", ".conda"}:
                path = path.with_suffix("")
            build = path.name.split("-")[-1]
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
                "depends": [f"{k} {v}".strip() for k, v in dep.dependencies.items()],
            }
            # mamba requires these to be stringlike so null are not allowed here
            if dep.hash.sha256 is not None:
                entry["sha256"] = dep.hash.sha256

            with open(conda_meta / (path.name + ".json"), "w") as f:
                json.dump(entry, f, indent=2)
        yield prefix
