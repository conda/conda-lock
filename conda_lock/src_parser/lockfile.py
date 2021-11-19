import pathlib

import toml

from . import Lockfile


def parse_conda_lock_file(
    path: pathlib.Path,
) -> Lockfile:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")

    content = toml.load(path)
    version = content.pop("version", None)
    if not (isinstance(version, int) and version <= Lockfile.version):
        raise ValueError(f"{path} has unknown version {version}")

    return Lockfile(**content)


def write_conda_lock_file(content: Lockfile, path: pathlib.Path) -> None:
    with path.open("w") as f:
        toml.dump(
            {
                "version": Lockfile.version,
                **content.dict(by_alias=True, exclude_unset=True),
            },
            f,
        )
