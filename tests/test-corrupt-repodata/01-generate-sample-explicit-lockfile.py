#!/usr/bin/env python3

import shlex
import subprocess

from pathlib import Path
from typing import Final


SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
DESTINATION_FILE: Final[Path] = SCRIPT_DIR / "01-explicit.lock"
DEFAULT_DESTINATION_LOCKFILE_RELATIVE_REPO_ROOT: Final[Path] = (
    Path() / "environments" / "conda-lock.yml"
)

COMMAND = """
    conda-lock render -p linux-64 --kind explicit \
        --filename-template DESTINATION_FILE FULL_LOCKFILE_PATH
"""


def main() -> None:
    """Main function."""
    repo_root = get_repo_root()
    full_lockfile_path = repo_root / DEFAULT_DESTINATION_LOCKFILE_RELATIVE_REPO_ROOT
    cmd = shlex.split(COMMAND)
    replacements = {
        "DESTINATION_FILE": str(DESTINATION_FILE),
        "FULL_LOCKFILE_PATH": str(full_lockfile_path),
    }
    replace_in_command(cmd, replacements)
    print(f"Running command: {shlex.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=repo_root)
    print(f"Generated: {DESTINATION_FILE}")


def get_repo_root() -> Path:
    """Get the root directory of the repository by finding the .git directory."""
    for parent in SCRIPT_DIR.parents:
        if (parent / ".git").exists():
            return parent
    raise FileNotFoundError("Could not find the root directory of the repository.")


def replace_in_command(split_command: list[str], replacements: dict[str, str]) -> None:
    """Replace the placeholders in the command with the replacements."""
    for i, arg in enumerate(split_command):
        if arg in replacements:
            split_command[i] = replacements[arg]


if __name__ == "__main__":
    main()
