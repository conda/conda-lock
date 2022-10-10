from tomlkit.exceptions import TOMLKitError

from conda_lock._vendor.poetry.core.exceptions import PoetryCoreException


class TOMLError(TOMLKitError, PoetryCoreException):
    pass
