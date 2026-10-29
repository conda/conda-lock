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

    def __init__(
        self, detail: str, *, recovery: str | None = None, report_bug: bool = False
    ) -> None:
        message = f"{detail}. Cannot safely construct the lockfile dependency graph."
        if recovery:
            message += f" {recovery}"
        if report_bug:
            message += (
                " If this occurred with metadata verification enabled, please report "
                "the inconsistent result at https://github.com/conda/conda-lock/issues "
                "with conda-lock/solver versions and a reproducible example; "
                "remove credentials from shared files and output. "
                "If verification was skipped, rerun with verification enabled first."
            )
        super().__init__(message)
