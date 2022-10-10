from typing import Any


class Infinity(object):
    def __repr__(self):  # type: () -> str
        return "Infinity"

    def __hash__(self):  # type: () -> int
        return hash(repr(self))

    def __lt__(self, other):  # type: (Any) -> bool
        return False

    def __le__(self, other):  # type: (Any) -> bool
        return False

    def __eq__(self, other):  # type: (Any) -> bool
        return isinstance(other, self.__class__)

    def __ne__(self, other):  # type: (Any) -> bool
        return not isinstance(other, self.__class__)

    def __gt__(self, other):  # type: (Any) -> bool
        return True

    def __ge__(self, other):  # type: (Any) -> bool
        return True

    def __neg__(self):  # type: () -> NegativeInfinity
        return NegativeInfinity


Infinity = Infinity()  # type: ignore


class NegativeInfinity(object):
    def __repr__(self):  # type: () -> str
        return "-Infinity"

    def __hash__(self):  # type: () -> int
        return hash(repr(self))

    def __lt__(self, other):  # type: (Any) -> bool
        return True

    def __le__(self, other):  # type: (Any) -> bool
        return True

    def __eq__(self, other):  # type: (Any) -> bool
        return isinstance(other, self.__class__)

    def __ne__(self, other):  # type: (Any) -> bool
        return not isinstance(other, self.__class__)

    def __gt__(self, other):  # type: (Any) -> bool
        return False

    def __ge__(self, other):  # type: (Any) -> bool
        return False

    def __neg__(self):  # type: () -> Infinity
        return Infinity


NegativeInfinity = NegativeInfinity()  # type: ignore
