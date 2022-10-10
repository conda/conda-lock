from typing import TYPE_CHECKING

from .base_constraint import BaseConstraint
from .empty_constraint import EmptyConstraint


if TYPE_CHECKING:
    from . import ConstraintTypes  # noqa


class AnyConstraint(BaseConstraint):
    def allows(self, other):  # type: ("ConstraintTypes") -> bool
        return True

    def allows_all(self, other):  # type: ("ConstraintTypes") -> bool
        return True

    def allows_any(self, other):  # type: ("ConstraintTypes") -> bool
        return True

    def difference(self, other):  # type: ("ConstraintTypes") -> "ConstraintTypes"
        if other.is_any():
            return EmptyConstraint()

        return other

    def intersect(self, other):  # type: ("ConstraintTypes") -> "ConstraintTypes"
        return other

    def union(self, other):  # type: ("ConstraintTypes") -> AnyConstraint
        return AnyConstraint()

    def is_any(self):  # type: () -> bool
        return True

    def is_empty(self):  # type: () -> bool
        return False

    def __str__(self):  # type: () -> str
        return "*"

    def __eq__(self, other):  # type: ("ConstraintTypes") -> bool
        return other.is_any()
