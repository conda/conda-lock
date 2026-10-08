class CondaLockError(Exception):
    """
    Generic conda-lock error.
    """


class PlatformValidationError(CondaLockError):
    """
    Error that is thrown when trying to install a lockfile that was built
    for a different platform.
    """


class MissingEnvVarError(CondaLockError):
    """
    Error thrown if env vars are missing in channel urls.
    """


class ChannelAggregationError(CondaLockError):
    """
    Error thrown when lists of channels cannot be combined
    """


class MetadataConsistencyError(CondaLockError):
    """The available records cannot establish a consistent package plan."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail}. Cannot safely construct the lockfile dependency graph. "
            "Regenerate from the original source files with a fresh package cache "
            "and a current solver, writing to a new lockfile path without --update. "
            "To locate additional caches, run `mamba config list pkgs_dirs --sources` "
            "(use `micromamba` instead for micromamba), or "
            "`conda config --show-sources` and `conda config --show pkgs_dirs`. "
            "For cache isolation and recovery commands, see "
            "https://conda.github.io/conda-lock/troubleshooting/#metadata-consistency-errors"
        )
