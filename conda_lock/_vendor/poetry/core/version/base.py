from typing import Callable


class BaseVersion:
    def __init__(self, version):  # type: (str) -> None
        self._version = str(version)
        self._key = None

    def __hash__(self):  # type: () -> int
        return hash(self._key)

    def __lt__(self, other):  # type: (BaseVersion) -> bool
        return self._compare(other, lambda s, o: s < o)

    def __le__(self, other):  # type: (BaseVersion) -> bool
        return self._compare(other, lambda s, o: s <= o)

    def __eq__(self, other):  # type: (BaseVersion) -> bool
        return self._compare(other, lambda s, o: s == o)

    def __ge__(self, other):  # type: (BaseVersion) -> bool
        return self._compare(other, lambda s, o: s >= o)

    def __gt__(self, other):  # type: (BaseVersion) -> bool
        return self._compare(other, lambda s, o: s > o)

    def __ne__(self, other):  # type: (BaseVersion) -> bool
        return self._compare(other, lambda s, o: s != o)

    def _compare(self, other, method):  # type: (BaseVersion, Callable) -> bool
        if not isinstance(other, BaseVersion):
            return NotImplemented

        return method(self._key, other._key)
