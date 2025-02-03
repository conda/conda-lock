from __future__ import annotations

from conda_lock._vendor.poetry.core.exceptions import PoetryCoreError


class PyProjectError(PoetryCoreError):
    pass
