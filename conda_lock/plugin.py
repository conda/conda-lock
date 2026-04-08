"""Conda plugin hooks for conda-lock.

Registers ``conda lock`` so the feedstock tooling is available as a conda
subcommand when this package is installed in the same environment as conda.
"""

from __future__ import annotations

from conda.plugins import hookimpl
from conda.plugins.types import CondaSubcommand


def _execute(args: tuple[str, ...]) -> int | None:
    """Dispatch plugin arguments to the lock CLI.

    Lazy import to avoid import-time side effects when not using conda-lock.
    """
    from conda_lock.__main__ import main

    return main()  # TODO: does not accept args for parsing since using click


@hookimpl
def conda_subcommands():
    yield CondaSubcommand(
        name="lock",
        summary=(
            "enerate fully reproducible lock files for conda environments."
        ),
        action=_execute,
    )
