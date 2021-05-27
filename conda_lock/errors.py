class CondaLockError(Exception):
    """
    Generic conda-lock error.
    """


class PlatformValidationError(CondaLockError):
    """
    Error that is thrown when trying to install a lockfile that was built
    for a different platform.
    """
