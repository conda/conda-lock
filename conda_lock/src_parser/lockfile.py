import pathlib

from typing import cast

import toml

from . import Lockfile


def parse_conda_lock_file(
    path: pathlib.Path,
) -> Lockfile:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")

    lockfile: Lockfile = cast(Lockfile, toml.load(path))

    return lockfile
