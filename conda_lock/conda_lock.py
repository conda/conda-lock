"""
Somewhat hacky solution to create conda lock files.
"""

import argparse
import atexit
import hashlib
import json
import logging
import os
import pathlib
import platform
import shutil
import stat
import subprocess
import sys
import tempfile

from typing import Dict, Iterable, List, MutableSequence, Optional, Set, Tuple, Union

import requests
import yaml


PathLike = Union[str, pathlib.Path]


if not (sys.version_info.major >= 3 and sys.version_info.minor >= 6):
    print("conda_lock needs to run under python >=3.6")
    sys.exit(1)


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]


def candidate_executables(conda_executable: Optional[str]) -> Iterable[Optional[str]]:
    if conda_executable:
        if pathlib.Path(conda_executable).exists():
            yield conda_executable
        yield shutil.which(conda_executable)
    yield shutil.which("conda")
    yield shutil.which("conda.exe")


def ensure_conda(conda_executable: Optional[str] = None) -> pathlib.Path:
    for candidate in candidate_executables(conda_executable):
        if candidate:
            return pathlib.Path(candidate)
    else:
        logging.info(
            "No existing conda installation found.  Installing the standalone conda solver"
        )
        return pathlib.Path(install_conda_exe())


def install_conda_exe() -> str:
    conda_exe_prefix = "https://repo.anaconda.com/pkgs/misc/conda-execs"
    if platform.system() == "Linux":
        conda_exe_file = "conda-latest-linux-64.exe"
    elif platform.system() == "Darwin":
        conda_exe_file = "conda-latest-osx-64.exe"
    elif platform.system() == "NT":
        conda_exe_file = "conda-latest-win-64.exe"
    else:
        # TODO: Support windows here
        raise ValueError(f"Unsupported platform: {platform.system()}")

    resp = requests.get(f"{conda_exe_prefix}/{conda_exe_file}", allow_redirects=True)
    resp.raise_for_status()
    target_filename = os.path.expanduser(pathlib.Path(__file__).parent / "conda.exe")
    with open(target_filename, "wb") as fo:
        fo.write(resp.content)
    st = os.stat(target_filename)
    os.chmod(target_filename, st.st_mode | stat.S_IXUSR)
    return target_filename


CONDA_PKGS_DIRS = None


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
    conda: PathLike, channels: List[str], specs: List[str], platform: str
) -> dict:
    args: MutableSequence[PathLike] = [
        conda,
        "create",
        "--prefix",
        pathlib.Path(conda_pkgs_dir()).joinpath("prefix"),
        "--override-channels",
        "--dry-run",
        "--json",
    ]
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
        print(f"    Command: {proc.args}")
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}")
        if proc.stderr:
            print(f"    STDOUT:\n{proc.stderr}")
        sys.exit(1)

    return json.loads(proc.stdout)


def search_for_md5s(conda: PathLike, package_specs: List[dict], platform: str):
    """Use conda-search to determine the md5 metadata that we need.

    This is only needed if pkgs_dirs is set in condarc.
    Sadly this is going to be slow since we need to fetch each result individually
    due to the cli of conda search

    """
    found: Set[str] = set()
    packages: List[Tuple[str, str]] = [
        *[(d["name"], f"{d['name']}[url={d['url_conda']}]") for d in package_specs],
        *[(d["name"], f"{d['name']}[url={d['url']}]") for d in package_specs],
    ]

    for name, spec in packages:
        if name in found:
            continue
        out = subprocess.run(
            ["conda", "search", "--use-index-cache", "--json", spec],
            encoding="utf8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=conda_env_override(platform),
        )
        content = json.loads(out.stdout)
        if name in content:
            assert len(content[name]) == 1
            yield content[name][0]
            found.add(name)


def parse_environment_file(environment_file: pathlib.Path) -> Dict:
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")
    with environment_file.open("r") as fo:
        env_yaml_data = yaml.safe_load(fo)
    # TODO: we basically ignore most of the fields for now.
    #       notable pip deps are just skipped below
    specs = env_yaml_data["dependencies"]
    channels = env_yaml_data.get("channels", [])

    # Split out any sub spec sections from the dependencies mapping
    mapping_specs = [x for x in specs if not isinstance(x, str)]
    specs = [x for x in specs if isinstance(x, str)]

    # Print a warning if there are pip specs in the dependencies
    for mapping_spec in mapping_specs:
        if "pip" in mapping_spec:
            print(
                (
                    "Warning, found pip deps not included in the lock file! You'll need to install "
                    "them separately"
                ),
                file=sys.stderr,
            )

    return {"specs": specs, "channels": channels}


def fn_to_dist_name(fn: str) -> str:
    if fn.endswith(".conda"):
        fn, _, _ = fn.partition(".conda")
    elif fn.endswith(".tar.bz2"):
        fn, _, _ = fn.partition(".tar.bz2")
    else:
        raise RuntimeError(f"unexpected file type {fn}", fn)
    return fn


def make_lock_files(
    conda: PathLike, platforms: List[str], channels: List[str], specs: List[str]
):
    for plat in platforms:
        print(f"generating lockfile for {plat}", file=sys.stderr)

        dry_run_install = solve_specs_for_arch(
            conda=conda, platform=plat, channels=channels, specs=specs
        )

        env_spec = json.dumps(
            {"channels": channels, "platform": plat, "specs": sorted(specs)},
            sort_keys=True,
        )
        env_hash: "hashlib._Hash" = hashlib.sha256(env_spec.encode("utf-8"))
        with open(f"conda-{plat}.lock", "w") as fo:
            fo.write(f"# platform: {plat}\n")
            fo.write(f"# env_hash: {env_hash.hexdigest()}\n")
            fo.write("@EXPLICIT\n")
            link_actions = dry_run_install["actions"]["LINK"]
            for link in link_actions:
                link[
                    "url_base"
                ] = f"{link['base_url']}/{link['platform']}/{link['dist_name']}"
                link["url"] = f"{link['url_base']}.tar.bz2"
                link["url_conda"] = f"{link['url_base']}.conda"
            link_dists = {link["dist_name"] for link in link_actions}

            fetch_actions = dry_run_install["actions"]["FETCH"]

            fetch_by_dist_name = {
                fn_to_dist_name(pkg["fn"]): pkg for pkg in fetch_actions
            }

            non_fetch_packages = link_dists - set(fetch_by_dist_name)
            if len(non_fetch_packages) > 0:
                for search_res in search_for_md5s(
                    conda,
                    [x for x in link_actions if x["dist_name"] in non_fetch_packages],
                    plat,
                ):
                    dist_name = fn_to_dist_name(search_res["fn"])
                    fetch_by_dist_name[dist_name] = search_res

            for pkg in link_actions:
                url = fetch_by_dist_name[pkg["dist_name"]]["url"]
                md5 = fetch_by_dist_name[pkg["dist_name"]]["md5"]
                fo.write(f"{url}#{md5}")
                fo.write("\n")

    print("To use the generated lock files create a new environment:", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "     conda create --name YOURENV --file conda-linux-64.lock", file=sys.stderr
    )
    print("", file=sys.stderr)


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


def parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--conda",
        default=None,
        help="path (or name) of the conda executable to use.",
    )
    parser.add_argument(
        "-p",
        "--platform",
        nargs="?",
        action="append",
        help="generate lock files for the following platforms",
    )
    parser.add_argument(
        "-f",
        "--file",
        default="environment.yml",
        help="path to a conda environment specification",
        type=lambda s: pathlib.Path(s),
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["default", "docker"],
        default="default",
        help="""
            Run this conda-lock in an isolated docker container.  This may be
            required to account for some issues where conda-lock conflicts with
            existing condarc configurations.
            """,
    )
    return parser


def run_lock(
    environment_file: pathlib.Path,
    conda_exe: Optional[str],
    platforms: Optional[List[str]] = None,
) -> None:
    desired_env = parse_environment_file(environment_file)
    _conda_exe = ensure_conda(conda_exe)
    make_lock_files(
        conda=_conda_exe,
        channels=desired_env["channels"] or [],
        specs=desired_env["specs"],
        platforms=platforms or DEFAULT_PLATFORMS,
    )


def main():
    args = parser().parse_args()
    run_lock(environment_file=args.file, conda_exe=args.conda, platforms=args.platform)


if __name__ == "__main__":
    main()
