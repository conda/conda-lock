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
        if recovery is None:
            recovery = (
                "Regenerate from the original source files with a fresh package cache "
                "and a current solver, writing to a new lockfile path without --update. "
                "To locate additional caches, run `mamba config list pkgs_dirs --sources` "
                "(use `micromamba` instead for micromamba), or "
                "`conda config --show-sources` and `conda config --show pkgs_dirs`."
            )
        if report_bug:
            recovery += (
                " If this occurred with metadata verification enabled, please report "
                "the inconsistent result at https://github.com/conda/conda-lock/issues "
                "with conda-lock/solver versions and a reproducible example; "
                "remove credentials from shared files and output."
            )
        super().__init__(
            f"{detail}. Cannot safely construct the lockfile dependency graph. "
            f"{recovery} "
            "For cache isolation and recovery commands, see "
            "https://conda.github.io/conda-lock/troubleshooting/#metadata-consistency-errors"
        )
