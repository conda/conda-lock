from typing import TYPE_CHECKING
from typing import Any
from typing import FrozenSet
from typing import List
from typing import Optional
from typing import Union

from conda_lock._vendor.poetry.core.semver import Version
from conda_lock._vendor.poetry.core.semver import VersionConstraint
from conda_lock._vendor.poetry.core.semver import VersionRange
from conda_lock._vendor.poetry.core.semver import VersionUnion
from conda_lock._vendor.poetry.core.semver import parse_constraint
from conda_lock._vendor.poetry.core.version.markers import AnyMarker
from conda_lock._vendor.poetry.core.version.markers import parse_marker

from .constraints import parse_constraint as parse_generic_constraint
from .constraints.constraint import Constraint
from .constraints.multi_constraint import MultiConstraint
from .constraints.union_constraint import UnionConstraint
from .specification import PackageSpecification
from .utils.utils import convert_markers


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.version.markers import BaseMarker  # noqa
    from conda_lock._vendor.poetry.core.packages import Package  # noqa
    from conda_lock._vendor.poetry.core.version.markers import VersionTypes  # noqa

    from .constraints import BaseConstraint  # noqa


class Dependency(PackageSpecification):
    def __init__(
        self,
        name,  # type: str
        constraint,  # type: Union[str, VersionConstraint]
        optional=False,  # type: bool
        category="main",  # type: str
        allows_prereleases=False,  # type: bool
        extras=None,  # type: Union[List[str], FrozenSet[str]]
        source_type=None,  # type: Optional[str]
        source_url=None,  # type: Optional[str]
        source_reference=None,  # type: Optional[str]
        source_resolved_reference=None,  # type: Optional[str]
    ):
        super(Dependency, self).__init__(
            name,
            source_type=source_type,
            source_url=source_url,
            source_reference=source_reference,
            source_resolved_reference=source_resolved_reference,
            features=extras,
        )

        self._constraint = None
        self.set_constraint(constraint=constraint)

        self._pretty_constraint = str(constraint)
        self._optional = optional
        self._category = category

        if isinstance(self._constraint, VersionRange) and self._constraint.min:
            allows_prereleases = (
                allows_prereleases or self._constraint.min.is_prerelease()
            )

        self._allows_prereleases = allows_prereleases

        self._python_versions = "*"
        self._python_constraint = parse_constraint("*")
        self._transitive_python_versions = None
        self._transitive_python_constraint = None
        self._transitive_marker = None
        self._extras = frozenset(extras or [])

        self._in_extras = []

        self._activated = not self._optional

        self.is_root = False
        self.marker = AnyMarker()
        self.source_name = None

    @property
    def name(self):  # type: () -> str
        return self._name

    @property
    def constraint(self):  # type: () -> "VersionTypes"
        return self._constraint

    def set_constraint(self, constraint):  # type: (Union[str, "VersionTypes"]) -> None
        try:
            if not isinstance(constraint, VersionConstraint):
                self._constraint = parse_constraint(constraint)
            else:
                self._constraint = constraint
        except ValueError:
            self._constraint = parse_constraint("*")

    @property
    def pretty_constraint(self):  # type: () -> str
        return self._pretty_constraint

    @property
    def pretty_name(self):  # type: () -> str
        return self._pretty_name

    @property
    def category(self):  # type: () -> str
        return self._category

    @property
    def python_versions(self):  # type: () -> str
        return self._python_versions

    @python_versions.setter
    def python_versions(self, value):  # type: (str) -> None
        self._python_versions = value
        self._python_constraint = parse_constraint(value)
        if not self._python_constraint.is_any():
            self.marker = self.marker.intersect(
                parse_marker(
                    self._create_nested_marker(
                        "python_version", self._python_constraint
                    )
                )
            )

    @property
    def transitive_python_versions(self):  # type: () -> str
        if self._transitive_python_versions is None:
            return self._python_versions

        return self._transitive_python_versions

    @transitive_python_versions.setter
    def transitive_python_versions(self, value):  # type: (str) -> None
        self._transitive_python_versions = value
        self._transitive_python_constraint = parse_constraint(value)

    @property
    def transitive_marker(self):  # type: () -> "BaseMarker"
        if self._transitive_marker is None:
            return self.marker

        return self._transitive_marker

    @transitive_marker.setter
    def transitive_marker(self, value):  # type: ("BaseMarker") -> None
        self._transitive_marker = value

    @property
    def python_constraint(self):  # type: () -> "VersionTypes"
        return self._python_constraint

    @property
    def transitive_python_constraint(self):  # type: () -> "VersionTypes"
        if self._transitive_python_constraint is None:
            return self._python_constraint

        return self._transitive_python_constraint

    @property
    def extras(self):  # type: () -> FrozenSet[str]
        return self._extras

    @property
    def in_extras(self):  # type: () -> list
        return self._in_extras

    @property
    def base_pep_508_name(self):  # type: () -> str
        requirement = self.pretty_name

        if self.extras:
            requirement += "[{}]".format(",".join(self.extras))

        if isinstance(self.constraint, VersionUnion):
            if self.constraint.excludes_single_version():
                requirement += " ({})".format(str(self.constraint))
            else:
                constraints = self.pretty_constraint.split(",")
                constraints = [parse_constraint(c) for c in constraints]
                constraints = [str(c) for c in constraints]
                requirement += " ({})".format(",".join(constraints))
        elif isinstance(self.constraint, Version):
            requirement += " (=={})".format(self.constraint.text)
        elif not self.constraint.is_any():
            requirement += " ({})".format(str(self.constraint).replace(" ", ""))

        return requirement

    def allows_prereleases(self):  # type: () -> bool
        return self._allows_prereleases

    def is_optional(self):  # type: () -> bool
        return self._optional

    def is_activated(self):  # type: () -> bool
        return self._activated

    def is_vcs(self):  # type: () -> bool
        return False

    def is_file(self):  # type: () -> bool
        return False

    def is_directory(self):  # type: () -> bool
        return False

    def is_url(self):  # type: () -> bool
        return False

    def accepts(self, package):  # type: (Package) -> bool
        """
        Determines if the given package matches this dependency.
        """
        return (
            self._name == package.name
            and self._constraint.allows(package.version)
            and (not package.is_prerelease() or self.allows_prereleases())
        )

    def to_pep_508(self, with_extras=True):  # type: (bool) -> str
        requirement = self.base_pep_508_name

        markers = []
        has_extras = False
        if not self.marker.is_any():
            marker = self.marker
            if not with_extras:
                marker = marker.without_extras()

            # we re-check for any marker here since the without extra marker might
            # return an any marker again
            if not marker.is_empty() and not marker.is_any():
                markers.append(str(marker))

            has_extras = "extra" in convert_markers(marker)
        else:
            # Python marker
            if self.python_versions != "*":
                python_constraint = self.python_constraint

                markers.append(
                    self._create_nested_marker("python_version", python_constraint)
                )

        in_extras = " || ".join(self._in_extras)
        if in_extras and with_extras and not has_extras:
            markers.append(
                self._create_nested_marker("extra", parse_generic_constraint(in_extras))
            )

        if markers:
            if self.is_vcs() or self.is_url():
                requirement += " "

            if len(markers) > 1:
                markers = ["({})".format(m) for m in markers]
                requirement += "; {}".format(" and ".join(markers))
            else:
                requirement += "; {}".format(markers[0])

        return requirement

    def _create_nested_marker(
        self, name, constraint
    ):  # type: (str, Union["BaseConstraint", Version, VersionConstraint]) -> str
        if isinstance(constraint, (MultiConstraint, UnionConstraint)):
            parts = []
            for c in constraint.constraints:
                multi = False
                if isinstance(c, (MultiConstraint, UnionConstraint)):
                    multi = True

                parts.append((multi, self._create_nested_marker(name, c)))

            glue = " and "
            if isinstance(constraint, UnionConstraint):
                parts = [
                    "({})".format(part[1]) if part[0] else part[1] for part in parts
                ]
                glue = " or "
            else:
                parts = [part[1] for part in parts]

            marker = glue.join(parts)
        elif isinstance(constraint, Constraint):
            marker = '{} {} "{}"'.format(name, constraint.operator, constraint.version)
        elif isinstance(constraint, VersionUnion):
            parts = []
            for c in constraint.ranges:
                parts.append(self._create_nested_marker(name, c))

            glue = " or "
            parts = ["({})".format(part) for part in parts]

            marker = glue.join(parts)
        elif isinstance(constraint, Version):
            if constraint.precision >= 3 and name == "python_version":
                name = "python_full_version"

            marker = '{} == "{}"'.format(name, constraint.text)
        else:
            if constraint.min is not None:
                min_name = name
                if constraint.min.precision >= 3 and name == "python_version":
                    min_name = "python_full_version"

                    if constraint.max is None:
                        name = min_name

                op = ">="
                if not constraint.include_min:
                    op = ">"

                version = constraint.min.text
                if constraint.max is not None:
                    max_name = name
                    if constraint.max.precision >= 3 and name == "python_version":
                        max_name = "python_full_version"

                    text = '{} {} "{}"'.format(min_name, op, version)

                    op = "<="
                    if not constraint.include_max:
                        op = "<"

                    version = constraint.max

                    text += ' and {} {} "{}"'.format(max_name, op, version)

                    return text
            elif constraint.max is not None:
                if constraint.max.precision >= 3 and name == "python_version":
                    name = "python_full_version"

                op = "<="
                if not constraint.include_max:
                    op = "<"

                version = constraint.max
            else:
                return ""

            marker = '{} {} "{}"'.format(name, op, version)

        return marker

    def activate(self):  # type: () -> None
        """
        Set the dependency as mandatory.
        """
        self._activated = True

    def deactivate(self):  # type: () -> None
        """
        Set the dependency as optional.
        """
        if not self._optional:
            self._optional = True

        self._activated = False

    def with_constraint(
        self, constraint
    ):  # type: (Union[str, VersionConstraint]) -> Dependency
        new = Dependency(
            self.pretty_name,
            constraint,
            optional=self.is_optional(),
            category=self.category,
            allows_prereleases=self.allows_prereleases(),
            extras=self._extras,
            source_type=self._source_type,
            source_url=self._source_url,
            source_reference=self._source_reference,
        )

        new.is_root = self.is_root
        new.python_versions = self.python_versions
        new.transitive_python_versions = self.transitive_python_versions
        new.marker = self.marker
        new.transitive_marker = self.transitive_marker

        for in_extra in self.in_extras:
            new.in_extras.append(in_extra)

        return new

    def __eq__(self, other):  # type: (Any) -> bool
        if not isinstance(other, Dependency):
            return NotImplemented

        return (
            self.is_same_package_as(other)
            and self._constraint == other.constraint
            and self._extras == other.extras
        )

    def __ne__(self, other):  # type: (Any) -> bool
        return not self == other

    def __hash__(self):  # type: () -> int
        return (
            super(Dependency, self).__hash__()
            ^ hash(self._constraint)
            ^ hash(self._extras)
        )

    def __str__(self):  # type: () -> str
        if self.is_root:
            return self._pretty_name
        return self.base_pep_508_name

    def __repr__(self):  # type: () -> str
        return "<{} {}>".format(self.__class__.__name__, str(self))
