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


class OrphanLockedDependencyError(CondaLockError):
    """
    Raised when planned packages cannot be reached from the
    requested input specs through declared ``dependencies`` edges.

    Such packages would silently vanish from the on-disk lockfile (the
    v1 serialization emits one entry per category, so an empty category
    set produces no entries) and the resulting environment would install
    fewer packages than the solver actually planned. An orphan is
    proof that the lockfile dependency graph is broken: every package
    in the plan must be reachable from some input spec.

    The usual root cause is a corrupt ``repodata_record.json`` from
    mamba/micromamba versions 2.1.1-2.3.3 (mamba-org/mamba#4052,
    mamba-org/mamba#4110) leaving the package -- *or one of its
    dependents* -- with empty ``depends``, which breaks the forward
    dependency walk. See conda/conda-lock#896.

    Emergency escape: ``CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE=1`` demotes
    the error to a loud warning and assigns orphans to ``main`` so
    they survive v1 serialization. This is intentionally ugly and is
    not a supported configuration knob.
    """
