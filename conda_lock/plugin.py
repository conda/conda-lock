"""Conda plugin hooks for conda-lock.

Registers ``conda lock`` so the feedstock tooling is available as a conda
subcommand when this package is installed in the same environment as conda.
"""

from __future__ import annotations


try:
    from conda.plugins import hookimpl  # type: ignore[unused-ignore]
    from conda.plugins.types import CondaSubcommand  # type: ignore[unused-ignore]

    HAVE_CONDA = True
except ImportError:
    HAVE_CONDA = False


if HAVE_CONDA:

    def _execute(args: tuple[str, ...]) -> int | None:
        """Dispatch plugin arguments to the lock CLI.

        Lazy import to avoid import-time side effects when not using conda-lock.
        """
        from conda_lock.conda_lock import main

        return main(args, standalone_mode=False)

    @hookimpl
    def conda_subcommands() -> CondaSubcommand:
        yield CondaSubcommand(
            name="lock",
            summary=("enerate fully reproducible lock files for conda environments."),
            action=_execute,
        )
