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
            "Also check for additional pkgs_dirs in your solver configuration."
        )
