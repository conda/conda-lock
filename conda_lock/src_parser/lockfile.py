import pathlib

import toml

from . import Lockfile


def parse_conda_lock_file(
    path: pathlib.Path,
) -> Lockfile:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")

    return Lockfile(**toml.load(path))
