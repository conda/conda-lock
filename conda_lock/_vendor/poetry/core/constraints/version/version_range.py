from __future__ import annotations

from contextlib import suppress
from functools import cached_property
from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.core.constraints.version.empty_constraint import EmptyConstraint
from conda_lock._vendor.poetry.core.constraints.version.version_constraint import _is_wildcard_candidate
from conda_lock._vendor.poetry.core.constraints.version.version_constraint import (
    _single_wildcard_range_string,
)
from conda_lock._vendor.poetry.core.constraints.version.version_range_constraint import (
    VersionRangeConstraint,
)
from conda_lock._vendor.poetry.core.constraints.version.version_union import VersionUnion


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.constraints.version.version import Version
    from conda_lock._vendor.poetry.core.constraints.version.version_constraint import VersionConstraint


def _is_canonical_strict_max(version: Version) -> bool:
    """True if ``version`` is the dev0 of a stable release — i.e., the
    canonical exclusive upper bound that the parser produces for a
    user-typed ``<V`` (with V stable).  See ``parser._canonical_strict_max``.
    """
    if version.dev is None or version.dev.number != 0:
        return False
    return not version.without_devrelease().is_unstable()


def _display_max_text(max_: Version, include_max: bool) -> str:
    """Render an exclusive ``<V.dev0`` (canonical-strict-max) back as ``<V``
    so user-facing strings match what the user typed."""
    if not include_max and _is_canonical_strict_max(max_):
        return max_.without_devrelease().text
    return max_.text


def _range_or_empty(
    min: Version | None = None,
    max: Version | None = None,
    include_min: bool = False,
    include_max: bool = False,
) -> VersionConstraint:
    """Construct a ``VersionRange``, returning ``EmptyConstraint`` if the
    constructed range is empty.  ``VersionRange.__init__`` cannot return a
    different type, so a tail-side normalisation is required for arithmetic
    operations whose result may be empty (e.g.
    ``VersionRange(V, V, True, False)`` is empty)."""
    range_ = VersionRange(min, max, include_min, include_max)
    if range_.is_empty():
        return EmptyConstraint()
    return range_


class VersionRange(VersionRangeConstraint):
    def __init__(
        self,
        min: Version | None = None,
        max: Version | None = None,
        include_min: bool = False,
        include_max: bool = False,
    ) -> None:
        self._max = max
        self._min = min
        self._include_min = include_min
        self._include_max = include_max

    @property
    def min(self) -> Version | None:
        return self._min

    @property
    def max(self) -> Version | None:
        return self._max

    @property
    def include_min(self) -> bool:
        return self._include_min

    @property
    def include_max(self) -> bool:
        return self._include_max

    def is_empty(self) -> bool:
        # A bounded range is non-empty only when its min is strictly below
        # its max, or when both bounds coincide and are inclusive (the
        # single-point range ``[V, V]``).  Coincident-bound non-inclusive
        # ranges and inverted ranges (min > max) are empty.  Previously
        # this unconditionally returned False.
        if self._min is None or self._max is None:
            return False
        if self._min == self._max:
            return not (self._include_min and self._include_max)
        return self._min > self._max

    def is_any(self) -> bool:
        return self._min is None and self._max is None

    def is_simple(self) -> bool:
        return self._min is None or self._max is None

    def allows(self, other: Version) -> bool:
        if self._min is not None:
            _this, _other = self.allowed_min, other

            assert _this is not None

            if (
                not self._include_min
                and not _this.is_postrelease()
                and _other.is_postrelease()
            ):
                # The exclusive ordered comparison >V MUST NOT allow a post-release
                # of the given version unless V itself is a post release.
                # https://peps.python.org/pep-0440/#exclusive-ordered-comparison
                # e.g. "2.0.post1" does not match ">2"
                _other = _other.without_postrelease()

            if not _this.is_local() and _other.is_local():
                # The exclusive ordered comparison >V MUST NOT match
                # a local version of the specified version.
                # https://peps.python.org/pep-0440/#exclusive-ordered-comparison
                # e.g. "2.0+local.version" does not match ">2"
                _other = _other.without_local()

            if _other < _this:
                return False

            if not self._include_min and (_other == self._min or _other == _this):
                return False

        if self.max is not None:
            _this, _other = self.max, other

            assert _this is not None

            if not _this.is_local() and _other.is_local():
                # allow weak equality to allow `3.0.0+local.1` for `<=3.0.0`
                _other = _other.without_local()

            if _other > _this:
                return False

            if not self._include_max and (_other == self._max or _other == _this):
                return False

        return True

    def allows_all(self, other: VersionConstraint) -> bool:
        from conda_lock._vendor.poetry.core.constraints.version.version import Version

        if other.is_empty():
            return True

        if isinstance(other, Version):
            if not self.allows(other):
                return False

            if other.is_local():
                # `==1.2.3+local` allows nothing but that very version.
                return True

            # A public version constraint also allows every local variant of
            # the version, so allowing `1.2.3` is not enough: `1.2.3+local`
            # has to be allowed, too. A bound that is itself a local variant
            # of `other` splits that band and leaves some of them out.
            #
            # Only the upper bound can be one: a local variant sorts above the
            # version it belongs to, so a range with such a lower bound does
            # not allow `other` in the first place and has returned already.
            return not (
                self._max is not None
                and self._max.is_local()
                and self._max.without_local() == other
            )

        if isinstance(other, VersionUnion):
            return all(self.allows_all(constraint) for constraint in other.ranges)

        if isinstance(other, VersionRangeConstraint):
            return not other.allows_lower(self) and not other.allows_higher(self)

        raise ValueError(f"Unknown VersionConstraint type {other}.")

    def allows_any(self, other: VersionConstraint) -> bool:
        from conda_lock._vendor.poetry.core.constraints.version.version import Version

        if other.is_empty():
            return False

        if isinstance(other, Version):
            if self.allows(other):
                return True

            # Although `>=1.2.3+local` does not allow the exact version `1.2.3`, both of
            # those versions do allow `1.2.3+local`.
            return (
                self.min is not None and self.min.is_local() and other.allows(self.min)
            )

        if isinstance(other, VersionUnion):
            return any(self.allows_any(constraint) for constraint in other.ranges)

        if isinstance(other, VersionRangeConstraint):
            return not (other.is_strictly_lower(self) or other.is_strictly_higher(self))

        raise ValueError(f"Unknown VersionConstraint type {other}.")

    def intersect(self, other: VersionConstraint) -> VersionConstraint:
        from conda_lock._vendor.poetry.core.constraints.version.version import Version

        if other.is_empty():
            return EmptyConstraint()

        if isinstance(other, VersionUnion):
            return other.intersect(self)

        if isinstance(other, Version):
            # A range and a Version just yields the version if it's in the range.
            if self.allows(other):
                return other

            # `>=1.2.3+local` intersects `1.2.3` to return `>=1.2.3+local,<1.2.4`.
            # Skip when ``other`` is itself a local version: for an exclusive
            # lower bound like ``>1.2.3+local`` intersected with the excluded
            # point ``1.2.3+local``, broadening would wrongly return a
            # non-empty range instead of the correct empty constraint.
            if (
                self.min is not None
                and self.min.is_local()
                and not other.is_local()
                and other.allows(self.min)
            ):
                # Strictly speaking, next_patch() is not quite correct
                # because you cannot specify the upper bound. It only
                # works for versions with a precision of three or less.
                upper = other.stable.next_patch()
                return _range_or_empty(
                    min=self.min,
                    max=upper,
                    include_min=self.include_min,
                    include_max=False,
                )

            return EmptyConstraint()

        if not isinstance(other, VersionRangeConstraint):
            raise TypeError(f"Unknown VersionConstraint type {other}.")

        if self.allows_lower(other):
            if self.is_strictly_lower(other):
                return EmptyConstraint()

            intersect_min = other.min
            intersect_include_min = other.include_min
        else:
            if other.is_strictly_lower(self):
                return EmptyConstraint()

            intersect_min = self._min
            intersect_include_min = self._include_min

        if self.allows_higher(other):
            intersect_max = other.max
            intersect_include_max = other.include_max
        else:
            intersect_max = self._max
            intersect_include_max = self._include_max

        if intersect_min is None and intersect_max is None:
            return VersionRange()

        # If the range is just a single version.
        if intersect_min == intersect_max:
            # Because we already verified that the lower range isn't strictly
            # lower, there must be some overlap.
            assert intersect_include_min and intersect_include_max
            assert intersect_min is not None

            return intersect_min

        # If we got here, there is an actual range.
        return _range_or_empty(
            intersect_min, intersect_max, intersect_include_min, intersect_include_max
        )

    def union(self, other: VersionConstraint) -> VersionConstraint:
        from conda_lock._vendor.poetry.core.constraints.version.version import Version

        if isinstance(other, Version):
            # If ``other`` is a public version that covers any local-tagged
            # variants at our bounds (by PEP 440 release-equality), the
            # union absorbs those local variants and we can broaden the
            # bound(s).  Handle both bounds at once so e.g. a range that is
            # bracketed by two local variants of ``other`` collapses to a
            # single release-spanning range.
            new_min: Version | None = self._min
            new_include_min = self._include_min
            new_max: Version | None = self._max
            new_include_max = self._include_max
            broadened = False
            if not other.is_local():
                if (
                    self._min is not None
                    and self._min.is_local()
                    and other.allows(self._min)
                ):
                    new_min = other
                    new_include_min = True
                    broadened = True
                if (
                    self._max is not None
                    and self._max.is_local()
                    and other.allows(self._max)
                ):
                    # Strictly speaking, next_patch() is not quite correct
                    # because you cannot specify the upper bound. It only
                    # works for versions with a precision of three or less.
                    new_max = other.stable.next_patch()
                    new_include_max = False
                    broadened = True
            if broadened:
                return _range_or_empty(
                    new_min, new_max, new_include_min, new_include_max
                )

            if self.allows(other):
                return self

            if other == self.min:
                return VersionRange(
                    self.min, self.max, include_min=True, include_max=self.include_max
                )

            if other == self.max:
                return VersionRange(
                    self.min, self.max, include_min=self.include_min, include_max=True
                )

            return VersionUnion.of(self, other)

        if isinstance(other, VersionRangeConstraint):
            # If the two ranges don't overlap, we won't be able to create a single
            # VersionRange for both of them.
            edges_touch = (
                self.max == other.min and (self.include_max or other.include_min)
            ) or (self.min == other.max and (self.include_min or other.include_max))

            if not edges_touch and not self.allows_any(other):
                return VersionUnion.of(self, other)

            if self.allows_lower(other):
                union_min = self.min
                union_include_min = self.include_min
            else:
                union_min = other.min
                union_include_min = other.include_min

            if self.allows_higher(other):
                union_max = self.max
                union_include_max = self.include_max
            else:
                union_max = other.max
                union_include_max = other.include_max

            return VersionRange(
                union_min,
                union_max,
                include_min=union_include_min,
                include_max=union_include_max,
            )

        return VersionUnion.of(self, other)

    def difference(self, other: VersionConstraint) -> VersionConstraint:
        from conda_lock._vendor.poetry.core.constraints.version.version import Version

        if other.is_empty():
            return self

        if isinstance(other, Version):
            if not self.allows(other):
                return self

            if other == self.min:
                if not self.include_min:
                    return self

                return _range_or_empty(self.min, self.max, False, self.include_max)

            if other == self.max:
                if not self.include_max:
                    return self

                return _range_or_empty(self.min, self.max, self.include_min, False)

            return VersionUnion.of(
                _range_or_empty(self.min, other, self.include_min, False),
                _range_or_empty(other, self.max, False, self.include_max),
            )
        elif isinstance(other, VersionRangeConstraint):
            if not self.allows_any(other):
                return self

            before: VersionConstraint | None
            if not self.allows_lower(other):
                before = None
            elif self.min == other.min:
                before = self.min
            else:
                before = _range_or_empty(
                    self.min, other.min, self.include_min, not other.include_min
                )

            after: VersionConstraint | None
            if not self.allows_higher(other):
                after = None
            elif self.max == other.max:
                after = self.max
            else:
                after = _range_or_empty(
                    other.max, self.max, not other.include_max, self.include_max
                )

            if before is None and after is None:
                return EmptyConstraint()

            if before is None:
                assert after is not None
                return after

            if after is None:
                return before

            return VersionUnion.of(before, after)
        elif isinstance(other, VersionUnion):
            ranges: list[VersionRangeConstraint] = []
            current: VersionRangeConstraint = self

            for range in other.ranges:
                # Skip any ranges that are strictly lower than [current].
                if range.is_strictly_lower(current):
                    continue

                # If we reach a range strictly higher than [current], no more ranges
                # will be relevant so we can bail early.
                if range.is_strictly_higher(current):
                    break

                difference = current.difference(range)
                if difference.is_empty():
                    return EmptyConstraint()
                elif isinstance(difference, VersionUnion):
                    # If [range] split [current] in half, we only need to continue
                    # checking future ranges against the latter half.
                    ranges.append(difference.ranges[0])
                    current = difference.ranges[-1]
                else:
                    assert isinstance(difference, VersionRangeConstraint)
                    current = difference

            if not ranges:
                return current

            return VersionUnion.of(*([*ranges, current]))

        raise ValueError(f"Unknown VersionConstraint type {other}.")

    def flatten(self) -> list[VersionRangeConstraint]:
        return [self]

    @cached_property
    def _single_wildcard_range_string(self) -> str:
        if not self.is_single_wildcard_range:
            raise ValueError("Not a valid wildcard range")

        assert self.min is not None
        assert self.max is not None
        return f"=={_single_wildcard_range_string(self.min, self.max)}"

    @cached_property
    def is_single_wildcard_range(self) -> bool:
        # e.g.
        # - "1.*" equals ">=1.0.dev0, <2" (equivalent to ">=1.0.dev0, <2.0.dev0")
        # - "1.0.*" equals ">=1.0.dev0, <1.1"
        # - "1.2.*" equals ">=1.2.dev0, <1.3"
        if (
            self.min is None
            or self.max is None
            or not self.include_min
            or self.include_max
        ):
            return False

        return _is_wildcard_candidate(self.min, self.max)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionRangeConstraint):
            return False

        return (
            self._min == other.min
            and self._max == other.max
            and self._include_min == other.include_min
            and self._include_max == other.include_max
        )

    def __lt__(self, other: VersionRangeConstraint) -> bool:
        return self._cmp(other) < 0

    def __le__(self, other: VersionRangeConstraint) -> bool:
        return self._cmp(other) <= 0

    def __gt__(self, other: VersionRangeConstraint) -> bool:
        return self._cmp(other) > 0

    def __ge__(self, other: VersionRangeConstraint) -> bool:
        return self._cmp(other) >= 0

    def _cmp(self, other: VersionRangeConstraint) -> int:
        if self.min is None:
            return self._compare_max(other) if other.min is None else -1
        elif other.min is None:
            return 1

        if self.min > other.min:
            return 1
        elif self.min < other.min:
            return -1

        if self.include_min != other.include_min:
            return -1 if self.include_min else 1

        return self._compare_max(other)

    def _compare_max(self, other: VersionRangeConstraint) -> int:
        if self.max is None:
            return 0 if other.max is None else 1
        elif other.max is None:
            return -1

        if self.max > other.max:
            return 1
        elif self.max < other.max:
            return -1

        if self.include_max != other.include_max:
            return 1 if self.include_max else -1

        return 0

    def __repr__(self) -> str:
        text = ""

        if self.min is not None:
            text += ">=" if self.include_min else ">"
            text += self.min.text

        if self.max is not None:
            if self.min is not None:
                text += ","

            op = "<=" if self.include_max else "<"
            # In contrast to __str__, we want to display the actual max so that
            # we can distinguish between `< 1` and `< 1.dev0` in test output.
            text += f"{op}{self.max}"

        if self.min is None and self.max is None:
            return "*"

        return f"<{self.__class__.__name__} {text}>"

    def __str__(self) -> str:
        with suppress(ValueError):
            return self._single_wildcard_range_string

        text = ""

        if self.min is not None:
            text += ">=" if self.include_min else ">"
            text += self.min.text

        if self.max is not None:
            if self.min is not None:
                text += ","

            op = "<=" if self.include_max else "<"
            text += f"{op}{_display_max_text(self.max, self.include_max)}"

        if self.min is None and self.max is None:
            return "*"

        return text

    def __hash__(self) -> int:
        return (
            hash(self.min)
            ^ hash(self.max)
            ^ hash(self.include_min)
            ^ hash(self.include_max)
        )
