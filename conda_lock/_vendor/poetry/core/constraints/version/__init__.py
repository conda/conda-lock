from __future__ import annotations

from conda_lock._vendor.poetry.core.constraints.version.empty_constraint import EmptyConstraint
from conda_lock._vendor.poetry.core.constraints.version.parser import parse_constraint
from conda_lock._vendor.poetry.core.constraints.version.parser import parse_marker_version_constraint
from conda_lock._vendor.poetry.core.constraints.version.util import constraint_regions
from conda_lock._vendor.poetry.core.constraints.version.version import Version
from conda_lock._vendor.poetry.core.constraints.version.version_constraint import VersionConstraint
from conda_lock._vendor.poetry.core.constraints.version.version_range import VersionRange
from conda_lock._vendor.poetry.core.constraints.version.version_range_constraint import (
    VersionRangeConstraint,
)
from conda_lock._vendor.poetry.core.constraints.version.version_union import VersionUnion


__all__ = (
    "EmptyConstraint",
    "Version",
    "VersionConstraint",
    "VersionRange",
    "VersionRangeConstraint",
    "VersionUnion",
    "constraint_regions",
    "parse_constraint",
    "parse_marker_version_constraint",
)
