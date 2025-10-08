#!/usr/bin/env python3
"""
A script to run the `ty` type checker for pre-commit via micromamba.

This script automates the process of running `ty check`. It ensures that the
conda-lock-dev environment is used for the type checker by leveraging micromamba.

The script performs the following steps:
1.  Changes to the repository root directory.
2.  Locates or installs a `micromamba` executable using the `ensureconda` package.
3.  Checks for the existence of a micromamba environment named 'conda-lock-dev'.
4.  If the environment does not exist, exits with an error and instructions.
5.  Verifies that the `ty` executable is available within the environment.
6.  Locates the `python` executable within the environment.
7.  Replaces the current process with `micromamba run ... ty check`, passing the
    located python executable and any passthrough arguments. This means that
    the exit code of this script will be the exit code of `ty check`.
"""

import json
import os
import shlex
import subprocess
import sys

from pathlib import Path

from ensureconda.api import ensureconda


def main(argv: list[str]) -> int:
    print("Preparing to run ty via micromamba...")
    print()

    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    print(f"Working directory: {root}")

    ensureconda_result = ensureconda(no_install=True)
    if ensureconda_result is None:
        print("Failed to locate conda/mamba/micromamba.", file=sys.stderr)
        sys.exit(1)
    conda_exe = Path(ensureconda_result)
    print(f"Conda executable found: {conda_exe}")

    env_name = "conda-lock-dev"
    env_file = root / "environments" / "conda-lock.yml"

    env_dict = get_env_list(conda_exe)
    if env_name not in env_dict:
        print(
            f"Environment '{env_name}' not found.\n"
            f"Please create it by running:\n"
            f"    micromamba env create --yes --name={env_name} --category=main --category=dev --file={env_file}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"{env_name} environment exists.")

    env_prefix = env_dict[env_name]
    print(f"Environment prefix: {env_prefix}")

    python_exe = get_python_executable(env_prefix)
    print(f"Python executable: {python_exe}")

    ensure_ty_available(env_prefix)

    print()
    return run_ty_check(conda_exe, env_prefix, argv)


def get_env_list(conda_exe: Path) -> dict[str, Path]:
    """Get mapping of environment names to paths from conda/mamba/micromamba."""
    result = subprocess.run(
        [conda_exe, "env", "list", "--json"],
        check=True,
        text=True,
        capture_output=True,
    )
    data = json.loads(result.stdout)
    return {Path(env_path).name: Path(env_path) for env_path in data["envs"]}


def get_python_executable(env_prefix: Path) -> Path:
    """Get the Python executable path from the environment prefix."""
    # On Windows, use Scripts/python.exe; on Unix, use bin/python
    if sys.platform == "win32":
        return env_prefix / "Scripts" / "python.exe"
    else:
        return env_prefix / "bin" / "python"


def ensure_ty_available(env_prefix: Path) -> None:
    """Verify that ty is available in the environment."""
    # On Windows, use Scripts/ty.exe; on Unix, use bin/ty
    if sys.platform == "win32":
        ty_exe = env_prefix / "Scripts" / "ty.exe"
    else:
        ty_exe = env_prefix / "bin" / "ty"

    if not ty_exe.exists():
        print(
            f"'ty' not found at {ty_exe}. "
            "Ensure it is included in environments/conda-lock.yml.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"ty executable: {ty_exe}")

    # Check ty version by running it directly
    try:
        result = subprocess.run(
            [str(ty_exe), "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"ty version: {result.stdout.strip()}")
    except subprocess.CalledProcessError:
        print(
            f"'ty' found at {ty_exe} but failed to run. "
            "The environment may be corrupted.",
            file=sys.stderr,
        )
        sys.exit(1)


def run_ty_check(conda_exe: Path, env_prefix: Path, argv: list[str]) -> int:
    args = [
        str(conda_exe),
        "run",
        # Use `--prefix` for micromamba, it's more explicit
        f"--prefix={env_prefix.as_posix()}",
        "ty",
        "check",
        # Pass the environment directory itself, which ty can use to find the interpreter.
        # This is more robust on Windows than passing the python.exe path directly.
        f"--python={env_prefix.as_posix()}",
        *argv,
    ]
    print("Running:")
    print(f"  {shlex.join(args)}")
    print()

    # Run ty and propagate its exit code. (Note: os.execvp doesn't propagate the exit code.)
    result = subprocess.run(args)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
