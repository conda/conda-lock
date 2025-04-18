from __future__ import annotations

import functools
import re

from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.core.constraints.generic.any_constraint import AnyConstraint
from conda_lock._vendor.poetry.core.constraints.generic.constraint import Constraint
from conda_lock._vendor.poetry.core.constraints.generic.union_constraint import UnionConstraint
from conda_lock._vendor.poetry.core.constraints.version.exceptions import ParseConstraintError


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.constraints.generic.base_constraint import BaseConstraint


BASIC_CONSTRAINT = re.compile(r"^(!?==?)?\s*([^\s]+?)\s*$")
STR_CMP_CONSTRAINT = re.compile(
    r"""(?ix)^ # case insensitive and verbose mode
    (?P<quote>['"]) # Single or double quotes
    (?P<value>.+?) # The value itself inside quotes
    \1 # Closing single of double quote
    \s* # Space
    (?P<op>(not\sin|in)) # Literal match of 'in' or 'not in'
    $"""
)


@functools.cache
def parse_constraint(constraints: str) -> BaseConstraint:
    if constraints == "*":
        return AnyConstraint()

    or_constraints = re.split(r"\s*\|\|?\s*", constraints.strip())
    or_groups = []
    for constraints in or_constraints:
        and_constraints = re.split(r"\s*,\s*", constraints)
        constraint_objects = []

        if len(and_constraints) > 1:
            for constraint in and_constraints:
                constraint_objects.append(parse_single_constraint(constraint))
        else:
            constraint_objects.append(parse_single_constraint(and_constraints[0]))

        if len(constraint_objects) == 1:
            constraint = constraint_objects[0]
        else:
            constraint = constraint_objects[0]
            for next_constraint in constraint_objects[1:]:
                constraint = constraint.intersect(next_constraint)

        or_groups.append(constraint)

    if len(or_groups) == 1:
        return or_groups[0]
    else:
        return UnionConstraint(*or_groups)


def parse_single_constraint(constraint: str) -> Constraint:
    # string comparator
    if m := STR_CMP_CONSTRAINT.match(constraint):
        op = m.group("op")
        value = m.group("value").strip()
        return Constraint(value, op)

    # Basic comparator

    if m := BASIC_CONSTRAINT.match(constraint):
        op = m.group(1)
        if op is None:
            op = "=="

        version = m.group(2).strip()

        return Constraint(version, op)

    raise ParseConstraintError(f"Could not parse version constraint: {constraint}")
