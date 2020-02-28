"""
Somewhat hacky solution to create conda lock files.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

from typing import Dict, List

import requests
import yaml


if not (sys.version_info.major >= 3 and sys.version_info.minor >= 6):
    print("conda_lock needs to run under python >=3.6")
    sys.exit(1)


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]


def solve_specs_for_arch(channels: List[str], specs: List[str], platform: str) -> str:
    env = dict(os.environ)
    with tempfile.TemporaryDirectory() as CONDA_PKGS_DIRS:
        env.update(
            {
                "CONDA_SUBDIR": platform,
                "CONDA_PKGS_DIRS": CONDA_PKGS_DIRS,
                "CONDA_UNSATISFIABLE_HINTS_CHECK_DEPTH": "0",
                "CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY": "False",
            }
        )

        args = [
            "conda",
            "create",
            "--prefix",
            str(pathlib.Path(CONDA_PKGS_DIRS).joinpath("prefix")),
            "--override-channels",
            "--dry-run",
            "--json",
        ]
        for channel in channels:
            args.extend(["--channel", channel])
        args.extend(specs)

        try:
            proc = subprocess.run(args, env=env, capture_output=True, encoding="utf8")
            proc.check_returncode()
        except Exception as e:
            err_json = json.loads(proc.stdout)
            import pprint

            pprint.pprint(err_json, indent=140)
            print(err_json["error"])
            print("\n")
            print(f"Could not lock the environment for platform {platform}")
            sys.exit(1)

        return json.loads(proc.stdout)


def parse_environment_file(environment_file: pathlib.Path) -> Dict:
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")
    with environment_file.open("r") as fo:
        env_yaml_data = yaml.safe_load(fo)
    # TODO: we basically ignore most of the fields for now.
    #       notable pip deps are not supported
    specs = env_yaml_data["dependencies"]
    channels = env_yaml_data.get("channels", [])
    return {"specs": specs, "channels": channels}


def fn_to_dist_name(fn: str) -> str:
    if fn.endswith(".conda"):
        fn, _, _ = fn.partition(".conda")
    elif fn.endswith(".tar.bz2"):
        fn, _, _ = fn.partition(".tar.bz2")
    else:
        raise RuntimeError(f"unexpected file type {fn}", fn)
    return fn


def make_lock_files(platforms, channels, specs):
    for platform in platforms:
        print(f"generating lockfile for {platform}", file=sys.stderr)
        dry_run_install = solve_specs_for_arch(
            platform=platform, channels=channels, specs=specs
        )
        with open(f"conda-{platform}.lock", "w") as fo:
            fo.write(f"# platform: {platform}\n")
            fo.write("@EXPLICIT\n")
            urls = {
                fn_to_dist_name(pkg["fn"]): pkg["url"]
                for pkg in dry_run_install["actions"]["FETCH"]
            }
            md5s = {
                fn_to_dist_name(pkg["fn"]): pkg["md5"]
                for pkg in dry_run_install["actions"]["FETCH"]
            }
            for pkg in dry_run_install["actions"]["LINK"]:
                url = urls[pkg["dist_name"]]
                md5 = md5s[pkg["dist_name"]]
                r = requests.head(url, allow_redirects=True)
                url = r.url
                fo.write(f"{url}#{md5}")
                fo.write("\n")

    print("To use the generated lock files create a new environment:", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "     conda create --name YOURENV --file conda-linux-64.lock", file=sys.stderr
    )
    print("", file=sys.stderr)


def main():
    import argparse

    parser = argparse.ArgumentParser()
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
    )

    args = parser.parse_args()

    environment_file = pathlib.Path(args.file)
    desired_env = parse_environment_file(environment_file)
    make_lock_files(
        channels=desired_env["channels"] or [],
        specs=desired_env["specs"],
        platforms=args.platform or DEFAULT_PLATFORMS,
    )


if __name__ == "__main__":
    main()
