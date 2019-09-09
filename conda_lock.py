"""
Somewhat hacky solution to create conda lock files.

This HAS to be executed from a python that has conda installed into its environment
    
TODO: This might be doable using to perform the inial solve withing resorting to copying over
    the solver from conda, but presently there is no way to have conda create use the solve 
    of another platform

    `conda create -n foo --json --dry-run specs

"""
from __future__ import absolute_import, print_function

import pathlib
import sys
import yaml

from conda.base.constants import UpdateModifier
from conda.base.context import context, reset_context
from conda.core.solve import DepsModifier, Solver
from conda.exceptions import (
    PackagesNotFoundError,
    ResolvePackageNotFound,
    SpecsConfigurationConflictError,
    UnsatisfiableError,
)

DEFAULT_PLATFORMS = ["osx-64", "linux-64", "win-64"]
PREFIX_NAME = "__magicmarker"


def solve_specs_for_arch(channels, specs, platform):
    # Harvested liberally from conda install 
    context.validate_configuration()

    repodata_fns = context.repodata_fns
    _should_retry_unfrozen = True

    for repodata_fn in repodata_fns:
        try:
            solver = Solver(
                prefix=PREFIX_NAME,
                channels=channels,
                subdirs=(platform, "noarch"),
                specs_to_add=specs,
                repodata_fn=repodata_fn,
            )
            deps_modifier = context.deps_modifier
            update_modifier = UpdateModifier.FREEZE_INSTALLED

            unlink_link_transaction = solver.solve_for_transaction(
                deps_modifier=deps_modifier,
                update_modifier=update_modifier,
                force_reinstall=False,
                should_retry_solve=(
                    _should_retry_unfrozen or repodata_fn != repodata_fns[-1]
                ),
            )

            break
        except (ResolvePackageNotFound, PackagesNotFoundError) as e:
            # end of the line.  Raise the exception
            if repodata_fn == repodata_fns[-1]:
                # PackagesNotFoundError is the only exception type we want to raise.
                #    Over time, we should try to get rid of ResolvePackageNotFound
                if isinstance(e, PackagesNotFoundError):
                    raise e

        except (UnsatisfiableError, SystemExit, SpecsConfigurationConflictError):
            if _should_retry_unfrozen:
                try:
                    unlink_link_transaction = solver.solve_for_transaction(
                        deps_modifier=deps_modifier,
                        update_modifier=UpdateModifier.UPDATE_SPECS,
                        force_reinstall=context.force_reinstall or context.force,
                        should_retry_solve=(repodata_fn != repodata_fns[-1]),
                    )
                except (
                    UnsatisfiableError,
                    SystemExit,
                    SpecsConfigurationConflictError,
                ) as e:
                    if repodata_fn != repodata_fns[-1]:
                        continue
                    else:
                        raise

            elif repodata_fn != repodata_fns[-1]:
                continue
            else:
                raise

    return unlink_link_transaction


def parse_environment_file(environment_file):
    # type: (pathlib.Path) -> list
    if not environment_file.exists:
        raise FileNotFoundError("environment.yml not found")
    with environment_file.open("r") as fo:
        env_yaml_data = yaml.safe_load(fo)
    # TODO: we basically ignore most of the field for now.
    #       notable pip deps are not supported
    specs = env_yaml_data["dependencies"]
    channels = env_yaml_data.get("channels", [])
    return {"specs": specs, "channels": channels}


def make_lock_files(platforms, channels, specs):
    for platform in platforms:
        print("generating lockfile for {}".format(platform), file=sys.stderr)
        state = solve_specs_for_arch(platform=platform, channels=channels, specs=specs)
        with open("conda-{}.lock".format(platform), "w") as fo:
            fo.write("# platform: {platform}\n".format(platform=platform))
            fo.write("@EXPLICIT\n")
            prefix_setup = state.prefix_setups[PREFIX_NAME]
            for ms in prefix_setup.link_precs:
                fo.write(ms.url)
                fo.write("\n")

    print("To use the generated lock files create a new environment:", file=sys.stderr)
    print("", file=sys.stderr)
    print("     conda create -n YOURENV --file conda-linux-64.lock", file=sys.stderr)
    print("", file=sys.stderr)


if __name__ == "__main__":
    reset_context()
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
        channels=desired_env["channels"] or context.channels,
        specs=desired_env["specs"],
        platforms=args.platform or DEFAULT_PLATFORMS,
    )
