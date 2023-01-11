from __future__ import annotations

from tomlkit.exceptions import TOMLKitError

from conda_lock._vendor.poetry.core.exceptions import PoetryCoreException


class TOMLError(TOMLKitError, PoetryCoreException):  # type: ignore[misc]
    pass
