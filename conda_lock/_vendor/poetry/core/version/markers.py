import os
import re

from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import Iterator
from typing import List
from typing import Union

from lark import Lark
from lark import Token
from lark import Tree


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.semver import VersionTypes  # noqa

MarkerTypes = Union[
    "AnyMarker", "EmptyMarker", "SingleMarker", "MultiMarker", "MarkerUnion"
]


class InvalidMarker(ValueError):
    """
    An invalid marker was found, users should refer to PEP 508.
    """


class UndefinedComparison(ValueError):
    """
    An invalid operation was attempted on a value that doesn't support it.
    """


class UndefinedEnvironmentName(ValueError):
    """
    A name was attempted to be used that does not exist inside of the
    environment.
    """


ALIASES = {
    "os.name": "os_name",
    "sys.platform": "sys_platform",
    "platform.version": "platform_version",
    "platform.machine": "platform_machine",
    "platform.python_implementation": "platform_python_implementation",
    "python_implementation": "platform_python_implementation",
}
_parser = Lark.open(
    os.path.join(os.path.dirname(__file__), "grammars", "markers.lark"), parser="lalr"
)


class BaseMarker(object):
    def intersect(self, other):  # type: (BaseMarker) -> BaseMarker
        raise NotImplementedError()

    def union(self, other):  # type: (BaseMarker) -> BaseMarker
        raise NotImplementedError()

    def is_any(self):  # type: () -> bool
        return False

    def is_empty(self):  # type: () -> bool
        return False

    def validate(self, environment):  # type: (Dict[str, Any]) -> bool
        raise NotImplementedError()

    def without_extras(self):  # type: () -> BaseMarker
        raise NotImplementedError()

    def exclude(self, marker_name):  # type: (str) -> BaseMarker
        raise NotImplementedError()

    def only(self, *marker_names):  # type: (str) -> BaseMarker
        raise NotImplementedError()

    def invert(self):  # type: () -> BaseMarker
        raise NotImplementedError()

    def __repr__(self):  # type: () -> str
        return "<{} {}>".format(self.__class__.__name__, str(self))


class AnyMarker(BaseMarker):
    def intersect(self, other):  # type: (MarkerTypes) -> MarkerTypes
        return other

    def union(self, other):  # type: (MarkerTypes) -> MarkerTypes
        return self

    def is_any(self):  # type: () -> bool
        return True

    def is_empty(self):  # type: () -> bool
        return False

    def validate(self, environment):  # type: (Dict[str, Any]) -> bool
        return True

    def without_extras(self):  # type: () -> MarkerTypes
        return self

    def exclude(self, marker_name):  # type: (str) -> MarkerTypes
        return self

    def only(self, *marker_names):  # type: (*str) -> MarkerTypes
        return self

    def invert(self):  # type: () -> EmptyMarker
        return EmptyMarker()

    def __str__(self):  # type: () -> str
        return ""

    def __repr__(self):  # type: () -> str
        return "<AnyMarker>"

    def __hash__(self):  # type: () -> int
        return hash(("<any>", "<any>"))

    def __eq__(self, other):  # type: (MarkerTypes) -> bool
        if not isinstance(other, BaseMarker):
            return NotImplemented

        return isinstance(other, AnyMarker)


class EmptyMarker(BaseMarker):
    def intersect(self, other):  # type: (MarkerTypes) -> MarkerTypes
        return self

    def union(self, other):  # type: (MarkerTypes) -> MarkerTypes
        return other

    def is_any(self):  # type: () -> bool
        return False

    def is_empty(self):  # type: () -> bool
        return True

    def validate(self, environment):  # type: (Dict[str, Any]) -> bool
        return False

    def without_extras(self):  # type: () -> BaseMarker
        return self

    def exclude(self, marker_name):  # type: (str) -> EmptyMarker
        return self

    def only(self, *marker_names):  # type: (*str) -> EmptyMarker
        return self

    def invert(self):  # type: () -> AnyMarker
        return AnyMarker()

    def __str__(self):  # type: () -> str
        return "<empty>"

    def __repr__(self):  # type: () -> str
        return "<EmptyMarker>"

    def __hash__(self):  # type: () -> int
        return hash(("<empty>", "<empty>"))

    def __eq__(self, other):  # type: (MarkerTypes) -> bool
        if not isinstance(other, BaseMarker):
            return NotImplemented

        return isinstance(other, EmptyMarker)


class SingleMarker(BaseMarker):

    _CONSTRAINT_RE = re.compile(r"(?i)^(~=|!=|>=?|<=?|==?=?|in|not in)?\s*(.+)$")
    _VERSION_LIKE_MARKER_NAME = {
        "python_version",
        "python_full_version",
        "platform_release",
    }

    def __init__(
        self, name, constraint
    ):  # type: (str, Union[str, "VersionTypes"]) -> None
        from conda_lock._vendor.poetry.core.packages.constraints import (
            parse_constraint as parse_generic_constraint,
        )
        from conda_lock._vendor.poetry.core.semver import parse_constraint

        self._name = ALIASES.get(name, name)
        self._constraint_string = str(constraint)

        # Extract operator and value
        m = self._CONSTRAINT_RE.match(self._constraint_string)
        self._operator = m.group(1)
        if self._operator is None:
            self._operator = "=="

        self._value = m.group(2)
        self._parser = parse_generic_constraint

        if name in self._VERSION_LIKE_MARKER_NAME:
            self._parser = parse_constraint

            if self._operator in {"in", "not in"}:
                versions = []
                for v in re.split("[ ,]+", self._value):
                    split = v.split(".")
                    if len(split) in [1, 2]:
                        split.append("*")
                        op = "" if self._operator == "in" else "!="
                    else:
                        op = "==" if self._operator == "in" else "!="

                    versions.append(op + ".".join(split))

                glue = ", "
                if self._operator == "in":
                    glue = " || "

                self._constraint = self._parser(glue.join(versions))
            else:
                self._constraint = self._parser(self._constraint_string)
        else:
            # if we have a in/not in operator we split the constraint
            # into a union/multi-constraint of single constraint
            constraint_string = self._constraint_string
            if self._operator in {"in", "not in"}:
                op, glue = ("==", " || ") if self._operator == "in" else ("!=", ", ")
                values = re.split("[ ,]+", self._value)
                constraint_string = glue.join(
                    ("{} {}".format(op, value) for value in values)
                )

            self._constraint = self._parser(constraint_string)

    @property
    def name(self):  # type: () -> str
        return self._name

    @property
    def constraint_string(self):  # type: () -> str
        if self._operator in {"in", "not in"}:
            return "{} {}".format(self._operator, self._value)

        return self._constraint_string

    @property
    def constraint(self):  # type: () -> "VersionTypes"
        return self._constraint

    @property
    def operator(self):  # type: () -> str
        return self._operator

    @property
    def value(self):  # type: () -> str
        return self._value

    def intersect(self, other):  # type: (MarkerTypes) -> MarkerTypes
        if isinstance(other, SingleMarker):
            if other.name != self.name:
                return MultiMarker(self, other)

            if self == other:
                return self

            if self._operator in {"in", "not in"} or other.operator in {"in", "not in"}:
                return MultiMarker.of(self, other)

            new_constraint = self._constraint.intersect(other.constraint)
            if new_constraint.is_empty():
                return EmptyMarker()

            if new_constraint == self._constraint or new_constraint == other.constraint:
                return SingleMarker(self._name, new_constraint)

            return MultiMarker.of(self, other)

        return other.intersect(self)

    def union(self, other):  # type: (MarkerTypes) -> MarkerTypes
        if isinstance(other, SingleMarker):
            if self == other:
                return self

            return MarkerUnion.of(self, other)

        return other.union(self)

    def validate(self, environment):  # type: (Dict[str, Any]) -> bool
        if environment is None:
            return True

        if self._name not in environment:
            return True

        return self._constraint.allows(self._parser(environment[self._name]))

    def without_extras(self):  # type: () -> MarkerTypes
        return self.exclude("extra")

    def exclude(self, marker_name):  # type: (str) -> MarkerTypes
        if self.name == marker_name:
            return AnyMarker()

        return self

    def only(self, *marker_names):  # type: (*str) -> Union[SingleMarker, EmptyMarker]
        if self.name not in marker_names:
            return EmptyMarker()

        return self

    def invert(self):  # type: () -> MarkerTypes
        if self._operator in ("===", "=="):
            operator = "!="
        elif self._operator == "!=":
            operator = "=="
        elif self._operator == ">":
            operator = "<="
        elif self._operator == ">=":
            operator = "<"
        elif self._operator == "<":
            operator = ">="
        elif self._operator == "<=":
            operator = ">"
        elif self._operator == "in":
            operator = "not in"
        elif self._operator == "not in":
            operator = "in"
        elif self._operator == "~=":
            # This one is more tricky to handle
            # since it's technically a multi marker
            # so the inverse will be a union of inverse
            from conda_lock._vendor.poetry.core.semver import VersionRange

            if not isinstance(self._constraint, VersionRange):
                # The constraint must be a version range, otherwise
                # it's an internal error
                raise RuntimeError(
                    "The '~=' operator should only represent version ranges"
                )

            min_ = self._constraint.min
            min_operator = ">=" if self._constraint.include_min else "<"
            max_ = self._constraint.max
            max_operator = "<=" if self._constraint.include_max else "<"

            return MultiMarker.of(
                SingleMarker(self._name, "{} {}".format(min_operator, min_)),
                SingleMarker(self._name, "{} {}".format(max_operator, max_)),
            ).invert()
        else:
            # We should never go there
            raise RuntimeError("Invalid marker operator '{}'".format(self._operator))

        return parse_marker("{} {} '{}'".format(self._name, operator, self._value))

    def __eq__(self, other):  # type: (MarkerTypes) -> bool
        if not isinstance(other, SingleMarker):
            return False

        return self._name == other.name and self._constraint == other.constraint

    def __hash__(self):  # type: () -> int
        return hash((self._name, self._constraint_string))

    def __str__(self):  # type: () -> str
        return '{} {} "{}"'.format(self._name, self._operator, self._value)


def _flatten_markers(
    markers, flatten_class
):  # type: (Iterator[Union[MarkerUnion, MultiMarker]], Any) -> List[MarkerTypes]
    flattened = []

    for marker in markers:
        if isinstance(marker, flatten_class):
            flattened += _flatten_markers(marker.markers, flatten_class)
        else:
            flattened.append(marker)

    return flattened


class MultiMarker(BaseMarker):
    def __init__(self, *markers):  # type: (*MarkerTypes) -> None
        self._markers = []

        markers = _flatten_markers(markers, MultiMarker)

        for m in markers:
            self._markers.append(m)

    @classmethod
    def of(cls, *markers):  # type: (*MarkerTypes) -> MarkerTypes
        new_markers = []
        markers = _flatten_markers(markers, MultiMarker)

        for marker in markers:
            if marker in new_markers:
                continue

            if marker.is_any():
                continue

            if isinstance(marker, SingleMarker):
                intersected = False
                for i, mark in enumerate(new_markers):
                    if (
                        not isinstance(mark, SingleMarker)
                        or isinstance(mark, SingleMarker)
                        and mark.name != marker.name
                    ):
                        continue

                    intersection = mark.constraint.intersect(marker.constraint)
                    if intersection == mark.constraint:
                        intersected = True
                    elif intersection == marker.constraint:
                        new_markers[i] = marker
                        intersected = True
                    elif intersection.is_empty():
                        return EmptyMarker()

                if intersected:
                    continue

            new_markers.append(marker)

        if any(m.is_empty() for m in new_markers) or not new_markers:
            return EmptyMarker()

        if len(new_markers) == 1 and new_markers[0].is_any():
            return AnyMarker()

        return MultiMarker(*new_markers)

    @property
    def markers(self):  # type: () -> List[MarkerTypes]
        return self._markers

    def intersect(self, other):  # type: (MarkerTypes) -> MarkerTypes
        if other.is_any():
            return self

        if other.is_empty():
            return other

        new_markers = self._markers + [other]

        return MultiMarker.of(*new_markers)

    def union(self, other):  # type: (MarkerTypes) -> MarkerTypes
        if isinstance(other, (SingleMarker, MultiMarker)):
            return MarkerUnion.of(self, other)

        return other.union(self)

    def validate(self, environment):  # type: (Dict[str, Any]) -> bool
        for m in self._markers:
            if not m.validate(environment):
                return False

        return True

    def without_extras(self):  # type: () -> MarkerTypes
        return self.exclude("extra")

    def exclude(self, marker_name):  # type: (str) -> MarkerTypes
        new_markers = []

        for m in self._markers:
            if isinstance(m, SingleMarker) and m.name == marker_name:
                # The marker is not relevant since it must be excluded
                continue

            marker = m.exclude(marker_name)

            if not marker.is_empty():
                new_markers.append(marker)

        return self.of(*new_markers)

    def only(self, *marker_names):  # type: (*str) -> MarkerTypes
        new_markers = []

        for m in self._markers:
            if isinstance(m, SingleMarker) and m.name not in marker_names:
                # The marker is not relevant since it's not one we want
                continue

            marker = m.only(*marker_names)

            if not marker.is_empty():
                new_markers.append(marker)

        return self.of(*new_markers)

    def invert(self):  # type: () -> MarkerTypes
        markers = [marker.invert() for marker in self._markers]

        return MarkerUnion.of(*markers)

    def __eq__(self, other):  # type: (MarkerTypes) -> bool
        if not isinstance(other, MultiMarker):
            return False

        return set(self._markers) == set(other.markers)

    def __hash__(self):  # type: () -> int
        h = hash("multi")
        for m in self._markers:
            h |= hash(m)

        return h

    def __str__(self):  # type: () -> str
        elements = []
        for m in self._markers:
            if isinstance(m, SingleMarker):
                elements.append(str(m))
            elif isinstance(m, MultiMarker):
                elements.append(str(m))
            else:
                elements.append("({})".format(str(m)))

        return " and ".join(elements)


class MarkerUnion(BaseMarker):
    def __init__(self, *markers):  # type: (*MarkerTypes) -> None
        self._markers = list(markers)

    @property
    def markers(self):  # type: () -> List[MarkerTypes]
        return self._markers

    @classmethod
    def of(cls, *markers):  # type: (*BaseMarker) -> MarkerTypes
        flattened_markers = _flatten_markers(markers, MarkerUnion)

        markers = []
        for marker in flattened_markers:
            if marker in markers:
                continue

            if isinstance(marker, SingleMarker) and marker.name == "python_version":
                intersected = False
                for i, mark in enumerate(markers):
                    if (
                        not isinstance(mark, SingleMarker)
                        or isinstance(mark, SingleMarker)
                        and mark.name != marker.name
                    ):
                        continue

                    intersection = mark.constraint.union(marker.constraint)
                    if intersection == mark.constraint:
                        intersected = True
                        break
                    elif intersection == marker.constraint:
                        markers[i] = marker
                        intersected = True
                        break

                if intersected:
                    continue

            markers.append(marker)

        if any(m.is_any() for m in markers):
            return AnyMarker()

        if not markers:
            return AnyMarker()

        if len(markers) == 1:
            return markers[0]

        return MarkerUnion(*markers)

    def append(self, marker):  # type: (MarkerTypes) -> None
        if marker in self._markers:
            return

        self._markers.append(marker)

    def intersect(self, other):  # type: (MarkerTypes) -> MarkerTypes
        if other.is_any():
            return self

        if other.is_empty():
            return other

        new_markers = []
        if isinstance(other, (SingleMarker, MultiMarker)):
            for marker in self._markers:
                intersection = marker.intersect(other)

                if not intersection.is_empty():
                    new_markers.append(intersection)
        elif isinstance(other, MarkerUnion):
            for our_marker in self._markers:
                for their_marker in other.markers:
                    intersection = our_marker.intersect(their_marker)

                    if not intersection.is_empty():
                        new_markers.append(intersection)

        return MarkerUnion.of(*new_markers)

    def union(self, other):  # type: (MarkerTypes) -> MarkerTypes
        if other.is_any():
            return other

        if other.is_empty():
            return self

        new_markers = self._markers + [other]

        return MarkerUnion.of(*new_markers)

    def validate(self, environment):  # type: (Dict[str, Any]) -> bool
        for m in self._markers:
            if m.validate(environment):
                return True

        return False

    def without_extras(self):  # type: () -> MarkerTypes
        return self.exclude("extra")

    def exclude(self, marker_name):  # type: (str) -> MarkerTypes
        new_markers = []

        for m in self._markers:
            if isinstance(m, SingleMarker) and m.name == marker_name:
                # The marker is not relevant since it must be excluded
                continue

            marker = m.exclude(marker_name)

            if not marker.is_empty():
                new_markers.append(marker)

        return self.of(*new_markers)

    def only(self, *marker_names):  # type: (*str) -> MarkerTypes
        new_markers = []

        for m in self._markers:
            if isinstance(m, SingleMarker) and m.name not in marker_names:
                # The marker is not relevant since it's not one we want
                continue

            marker = m.only(*marker_names)

            if not marker.is_empty():
                new_markers.append(marker)

        return self.of(*new_markers)

    def invert(self):  # type: () -> MarkerTypes
        markers = [marker.invert() for marker in self._markers]

        return MultiMarker.of(*markers)

    def __eq__(self, other):  # type: (MarkerTypes) -> bool
        if not isinstance(other, MarkerUnion):
            return False

        return set(self._markers) == set(other.markers)

    def __hash__(self):  # type: () -> int
        h = hash("union")
        for m in self._markers:
            h |= hash(m)

        return h

    def __str__(self):  # type: () -> str
        return " or ".join(
            str(m) for m in self._markers if not m.is_any() and not m.is_empty()
        )

    def is_any(self):  # type: () -> bool
        return any(m.is_any() for m in self._markers)

    def is_empty(self):  # type: () -> bool
        return all(m.is_empty() for m in self._markers)


def parse_marker(marker):  # type: (str) -> MarkerTypes
    if marker == "<empty>":
        return EmptyMarker()

    if not marker or marker == "*":
        return AnyMarker()

    parsed = _parser.parse(marker)

    markers = _compact_markers(parsed.children)

    return markers


def _compact_markers(tree_elements, tree_prefix=""):  # type: (Tree, str) -> MarkerTypes
    groups = [MultiMarker()]
    for token in tree_elements:
        if isinstance(token, Token):
            if token.type == "{}BOOL_OP".format(tree_prefix) and token.value == "or":
                groups.append(MultiMarker())

            continue

        if token.data == "marker":
            groups[-1] = MultiMarker.of(
                groups[-1], _compact_markers(token.children, tree_prefix=tree_prefix)
            )
        elif token.data == "{}item".format(tree_prefix):
            name, op, value = token.children
            if value.type == "{}MARKER_NAME".format(tree_prefix):
                name, value, = value, name

            value = value[1:-1]
            groups[-1] = MultiMarker.of(
                groups[-1], SingleMarker(name, "{}{}".format(op, value))
            )
        elif token.data == "{}BOOL_OP".format(tree_prefix):
            if token.children[0] == "or":
                groups.append(MultiMarker())

    for i, group in enumerate(reversed(groups)):
        if group.is_empty():
            del groups[len(groups) - 1 - i]
            continue

        if isinstance(group, MultiMarker) and len(group.markers) == 1:
            groups[len(groups) - 1 - i] = group.markers[0]

    if not groups:
        return EmptyMarker()

    if len(groups) == 1:
        return groups[0]

    return MarkerUnion.of(*groups)
