from __future__ import annotations

from conda_lock._vendor.poetry.core.constraints.version.patterns import BASIC_CONSTRAINT
from conda_lock._vendor.poetry.core.constraints.version.patterns import CARET_CONSTRAINT
from conda_lock._vendor.poetry.core.constraints.version.patterns import COMPLETE_VERSION
from conda_lock._vendor.poetry.core.constraints.version.patterns import TILDE_CONSTRAINT
from conda_lock._vendor.poetry.core.constraints.version.patterns import TILDE_PEP440_CONSTRAINT
from conda_lock._vendor.poetry.core.constraints.version.patterns import X_CONSTRAINT


__all__ = [
    "COMPLETE_VERSION",
    "CARET_CONSTRAINT",
    "TILDE_CONSTRAINT",
    "TILDE_PEP440_CONSTRAINT",
    "X_CONSTRAINT",
    "BASIC_CONSTRAINT",
]
