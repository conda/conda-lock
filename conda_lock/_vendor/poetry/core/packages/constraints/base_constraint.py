from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from . import ConstraintTypes  # noqa


class BaseConstraint(object):
    def allows(self, other):  # type: ("ConstraintTypes") -> bool
        raise NotImplementedError

    def allows_all(self, other):  # type: ("ConstraintTypes") -> bool
        raise NotImplementedError()

    def allows_any(self, other):  # type: ("ConstraintTypes") -> bool
        raise NotImplementedError()

    def difference(self, other):  # type: ("ConstraintTypes") -> "ConstraintTypes"
        raise NotImplementedError()

    def intersect(self, other):  # type: ("ConstraintTypes") -> "ConstraintTypes"
        raise NotImplementedError()

    def union(self, other):  # type: ("ConstraintTypes") -> "ConstraintTypes"
        raise NotImplementedError()

    def is_any(self):  # type: () -> bool
        return False

    def is_empty(self):  # type: () -> bool
        return False

    def __repr__(self):  # type: () -> str
        return "<{} {}>".format(self.__class__.__name__, str(self))

    def __eq__(self, other):  # type: ("ConstraintTypes") -> bool
        raise NotImplementedError()
