from typing import TYPE_CHECKING

from .base_constraint import BaseConstraint


if TYPE_CHECKING:
    from . import ConstraintTypes  # noqa


class EmptyConstraint(BaseConstraint):

    pretty_string = None

    def matches(self, _):  # type: ("ConstraintTypes") -> bool
        return True

    def is_empty(self):  # type: () -> bool
        return True

    def allows(self, other):  # type: ("ConstraintTypes") -> bool
        return False

    def allows_all(self, other):  # type: ("ConstraintTypes") -> bool
        return True

    def allows_any(self, other):  # type: ("ConstraintTypes") -> bool
        return True

    def intersect(self, other):  # type: ("ConstraintTypes") -> "ConstraintTypes"
        return other

    def difference(self, other):  # type: ("ConstraintTypes") -> None
        return

    def __eq__(self, other):  # type: ("ConstraintTypes") -> bool
        return other.is_empty()

    def __str__(self):  # type: () -> str
        return ""
