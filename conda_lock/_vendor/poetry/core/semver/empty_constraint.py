from typing import TYPE_CHECKING

from .version_constraint import VersionConstraint


if TYPE_CHECKING:
    from . import VersionTypes  # noqa
    from .version import Version  # noqa


class EmptyConstraint(VersionConstraint):
    def is_empty(self):  # type: () -> bool
        return True

    def is_any(self):  # type: () -> bool
        return False

    def allows(self, version):  # type: ("Version") -> bool
        return False

    def allows_all(self, other):  # type: ("VersionTypes") -> bool
        return other.is_empty()

    def allows_any(self, other):  # type: ("VersionTypes") -> bool
        return False

    def intersect(self, other):  # type: ("VersionTypes") -> EmptyConstraint
        return self

    def union(self, other):  # type: ("VersionTypes") -> "VersionTypes"
        return other

    def difference(self, other):  # type: ("VersionTypes") -> EmptyConstraint
        return self

    def __str__(self):  # type: () -> str
        return "<empty>"
