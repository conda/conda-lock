from __future__ import annotations

from conda_lock._vendor.poetry.core.constraints.version.parser import parse_constraint
from conda_lock._vendor.poetry.core.constraints.version.parser import parse_single_constraint


__all__ = ["parse_constraint", "parse_single_constraint"]
