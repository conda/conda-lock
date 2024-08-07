from __future__ import annotations

from conda_lock._vendor.poetry.core.exceptions import PoetryCoreException
from tomlkit.exceptions import TOMLKitError


class TOMLError(TOMLKitError, PoetryCoreException):
    pass
