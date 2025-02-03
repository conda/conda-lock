from __future__ import annotations

import operator

from typing import Callable
from typing import ClassVar

from conda_lock._vendor.poetry.core.constraints.generic.any_constraint import AnyConstraint
from conda_lock._vendor.poetry.core.constraints.generic.base_constraint import BaseConstraint
from conda_lock._vendor.poetry.core.constraints.generic.empty_constraint import EmptyConstraint


OperatorType = Callable[[object, object], bool]


def contains(a: object, b: object, /) -> bool:
    return operator.contains(a, b)  # type: ignore[arg-type]


def not_contains(a: object, b: object, /) -> bool:
    return not contains(a, b)


class Constraint(BaseConstraint):
    OP_EQ = operator.eq
    OP_NE = operator.ne
    OP_IN = contains
    OP_NC = not_contains

    _trans_op_str: ClassVar[dict[str, OperatorType]] = {
        "=": OP_EQ,
        "==": OP_EQ,
        "!=": OP_NE,
        "in": OP_IN,
        "not in": OP_NC,
    }

    _trans_op_int: ClassVar[dict[OperatorType, str]] = {
        OP_EQ: "==",
        OP_NE: "!=",
        OP_IN: "in",
        OP_NC: "not in",
    }

    _trans_op_inv: ClassVar[dict[str, str]] = {
        "!=": "==",
        "==": "!=",
        "not in": "in",
        "in": "not in",
    }

    def __init__(self, value: str, operator: str = "==") -> None:
        if operator == "=":
            operator = "=="

        self._value = value
        self._operator = operator
        self._op = self._trans_op_str[operator]

    @property
    def value(self) -> str:
        return self._value

    @property
    def operator(self) -> str:
        return self._operator

    def allows(self, other: BaseConstraint) -> bool:
        if not isinstance(other, Constraint) or other.operator != "==":
            raise ValueError(
                f"Invalid argument for allows"
                f' ("other" must be a constraint with operator "=="): {other}'
            )

        if op := self._trans_op_str.get(self._operator):
            return op(other.value, self._value)

        return False

    def allows_all(self, other: BaseConstraint) -> bool:
        from conda_lock._vendor.poetry.core.constraints.generic import MultiConstraint
        from conda_lock._vendor.poetry.core.constraints.generic import UnionConstraint

        if isinstance(other, Constraint):
            if other.operator == "==":
                return self.allows(other)

            if other.operator == "in" and self._operator == "in":
                return self.value in other.value

            if other.operator == "not in":
                if self._operator == "not in":
                    return other.value in self.value
                if self._operator == "!=":
                    return self.value not in other.value

            return self == other

        if isinstance(other, MultiConstraint):
            return any(self.allows_all(c) for c in other.constraints)

        if isinstance(other, UnionConstraint):
            return all(self.allows_all(c) for c in other.constraints)

        return other.is_empty()

    def allows_any(self, other: BaseConstraint) -> bool:
        from conda_lock._vendor.poetry.core.constraints.generic import MultiConstraint
        from conda_lock._vendor.poetry.core.constraints.generic import UnionConstraint

        if self._operator == "==":
            return other.allows(self)

        if isinstance(other, Constraint):
            if other.operator == "==":
                return self.allows(other)

            if other.operator == "!=" and self._operator == "==":
                return self._value != other.value

            if other.operator == "not in" and self._operator == "in":
                return other.value not in self.value

            if other.operator == "in" and self._operator == "not in":
                return self.value not in other.value

            return True

        elif isinstance(other, MultiConstraint):
            return self._operator == "!="

        elif isinstance(other, UnionConstraint):
            return self._operator == "!=" and any(
                self.allows_any(c) for c in other.constraints
            )

        return other.is_any()

    def invert(self) -> Constraint:
        return Constraint(self._value, self._trans_op_inv[self.operator])

    def difference(self, other: BaseConstraint) -> Constraint | EmptyConstraint:
        if other.allows(self):
            return EmptyConstraint()

        return self

    def intersect(self, other: BaseConstraint) -> BaseConstraint:
        from conda_lock._vendor.poetry.core.constraints.generic.multi_constraint import MultiConstraint

        if isinstance(other, Constraint):
            if other == self:
                return self

            if self.allows_all(other):
                return other

            if other.allows_all(self):
                return self

            if not self.allows_any(other) or not other.allows_any(self):
                return EmptyConstraint()

            return MultiConstraint(self, other)

        return other.intersect(self)

    def union(self, other: BaseConstraint) -> BaseConstraint:
        from conda_lock._vendor.poetry.core.constraints.generic.union_constraint import UnionConstraint

        if isinstance(other, Constraint):
            if other == self:
                return self

            if self.allows_all(other):
                return self

            if other.allows_all(self):
                return other

            ops = {self.operator, other.operator}
            if (
                (ops in ({"!="}, {"not in"}))
                or (
                    (
                        ops in ({"in", "!="}, {"in", "not in"})
                        and (self.operator == "in" and self.value in other.value)
                    )
                    or (other.operator == "in" and other.value in self.value)
                )
                or self.invert() == other
            ):
                return AnyConstraint()

            return UnionConstraint(self, other)

        # to preserve order (functionally not necessary)
        if isinstance(other, UnionConstraint):
            return UnionConstraint(self).union(other)

        return other.union(self)

    def is_any(self) -> bool:
        return False

    def is_empty(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Constraint):
            return NotImplemented

        return (self.value, self.operator) == (other.value, other.operator)

    def __hash__(self) -> int:
        return hash((self._operator, self._value))

    def __str__(self) -> str:
        if self._operator in {"in", "not in"}:
            return f"'{self._value}' {self._operator}"
        op = self._operator if self._operator != "==" else ""
        return f"{op}{self._value}"
