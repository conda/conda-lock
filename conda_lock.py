#!/usr/bin/env python3
"""
Somewhat hacky solution to create conda lock files.
"""

import json
import sys

if not (sys.version_info.major >= 3 and sys.version_info.minor >= 6):
    print("conda_lock needs to run under python >=3.6")
    sys.exit(1)

import pathlib
import subprocess
import sys
import yaml
import os
import requests
import tempfile


DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]


def solve_specs_for_arch(channels, specs, platform):
    # type: (typing.List[str], typing.List[str], str) -> dict

    env = dict(os.environ)
    with tempfile.TemporaryDirectory() as CONDA_PKGS_DIRS:
        env.update({"CONDA_SUBDIR": platform, "CONDA_PKGS_DIRS": CONDA_PKGS_DIRS})

        args = [
            "conda",
            "create",
            "--prefix",
            f"{CONDA_PKGS_DIRS}_prefix",
            "--override-channels",
            "--dry-run",
            "--json",
        ]
        for channel in channels:
            args.extend(["--channel", channel])
        args.extend(specs)

        json_output = subprocess.check_output(args, env=env)
    return json.loads(json_output)


def parse_environment_file(environment_file):
    # type: (pathlib.Path) -> list
    if not environment_file.exists():
        raise FileNotFoundError("{} not found".format(environment_file))
    with environment_file.open("r") as fo:
        env_yaml_data = yaml.safe_load(fo)
    # TODO: we basically ignore most of the fields for now.
    #       notable pip deps are not supported
    specs = env_yaml_data["dependencies"]
    channels = env_yaml_data.get("channels", [])
    return {"specs": specs, "channels": channels}


def fn_to_dist_name(fn):
    fn, _, _ = fn.partition('.conda')
    fn, _, _ = fn.partition('.tar.bz2')
    return fn

def make_lock_files(platforms, channels, specs):
    for platform in platforms:
        print("generating lockfile for {}".format(platform), file=sys.stderr)
        dry_run_install = solve_specs_for_arch(
            platform=platform, channels=channels, specs=specs
        )
        with open("conda-{}.lock".format(platform), "w") as fo:
            fo.write("# platform: {platform}\n".format(platform=platform))
            fo.write("@EXPLICIT\n")
            urls = {
                fn_to_dist_name(pkg['fn']): pkg['url'] for pkg in dry_run_install["actions"]["FETCH"]
            }
            for pkg in dry_run_install["actions"]["LINK"]:
                url = urls[pkg["dist_name"]]
                r = requests.head(url, allow_redirects=True)
                url = r.url
                fo.write(url)
                fo.write("\n")

    print("To use the generated lock files create a new environment:", file=sys.stderr)
    print("", file=sys.stderr)
    print("     conda create --name YOURENV --file conda-linux-64.lock", file=sys.stderr)
    print("", file=sys.stderr)


if __name__ == "__main__":
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
