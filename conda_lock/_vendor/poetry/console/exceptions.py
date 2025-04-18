from __future__ import annotations

from conda_lock._vendor.cleo.exceptions import CleoError


class PoetryConsoleError(CleoError):
    pass


class GroupNotFoundError(PoetryConsoleError):
    pass
