from __future__ import annotations

import functools
import re

from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.core.constraints.version.exceptions import ParseConstraintError
from conda_lock._vendor.poetry.core.version.exceptions import InvalidVersionError


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.constraints.version.version import Version
    from conda_lock._vendor.poetry.core.constraints.version.version_constraint import VersionConstraint


def _canonical_strict_max(
    version: Version, *, is_marker_constraint: bool = False
) -> Version:
    """Canonicalize an exclusive ``<V`` upper bound.

    Per PEP 440, ``<V`` for stable V MUST NOT allow any pre-/dev-release of V,
    so the effective max is ``V.dev0``.  Applied at parse-time so that user
    intent (``<V``) is captured in the data structure; internal range
    arithmetic preserves raw maxes so that, e.g., ``(>1, <3) - {2}`` allows
    ``2.dev0`` in the interior fragment ``(1, 2)``.

    In a marker-constraint context (``python_version`` etc.) the observable
    values are always concrete release strings, so canonicalization would be
    a semantic no-op; we skip it there to keep marker rendering stable.
    """
    if is_marker_constraint or version.is_unstable():
        return version
    return version.first_devrelease()


@functools.cache
def parse_constraint(constraints: str) -> VersionConstraint:
    return _parse_constraint(constraints=constraints)


@functools.cache
def parse_marker_version_constraint(
    constraints: str, *, pep440: bool = True
) -> VersionConstraint:
    return _parse_constraint(
        constraints=constraints, is_marker_constraint=True, pep440=pep440
    )


def _parse_constraint(
    constraints: str, *, is_marker_constraint: bool = False, pep440: bool = True
) -> VersionConstraint:
    if constraints == "*":
        from conda_lock._vendor.poetry.core.constraints.version.version_range import VersionRange

        return VersionRange()

    or_constraints = re.split(r"\s*\|\|?\s*", constraints.strip())
    or_groups = []
    for or_constraint in or_constraints:
        # allow trailing commas for robustness (even though it may not be
        # standard-compliant it seems to occur in some packages)
        or_constraint = or_constraint.rstrip(",").rstrip()
        and_constraints = re.split(
            r"(?<!^)(?<![\^~=>< ,]) *(?<!-)[, ](?!-) *(?!,|$)", or_constraint
        )
        constraint_objects = []

        if len(and_constraints) > 1:
            for constraint in and_constraints:
                constraint_objects.append(
                    parse_single_constraint(
                        constraint,
                        is_marker_constraint=is_marker_constraint,
                        pep440=pep440,
                    )
                )
        else:
            constraint_objects.append(
                parse_single_constraint(
                    and_constraints[0],
                    is_marker_constraint=is_marker_constraint,
                    pep440=pep440,
                )
            )

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
        from conda_lock._vendor.poetry.core.constraints.version.version_union import VersionUnion

        return VersionUnion.of(*or_groups)


def parse_single_constraint(
    constraint: str, *, is_marker_constraint: bool = False, pep440: bool = True
) -> VersionConstraint:
    from conda_lock._vendor.poetry.core.constraints.version.patterns import BASIC_CONSTRAINT
    from conda_lock._vendor.poetry.core.constraints.version.patterns import BASIC_RELEASE_CONSTRAINT
    from conda_lock._vendor.poetry.core.constraints.version.patterns import CARET_CONSTRAINT
    from conda_lock._vendor.poetry.core.constraints.version.patterns import TILDE_CONSTRAINT
    from conda_lock._vendor.poetry.core.constraints.version.patterns import TILDE_PEP440_CONSTRAINT
    from conda_lock._vendor.poetry.core.constraints.version.patterns import X_CONSTRAINT
    from conda_lock._vendor.poetry.core.constraints.version.version import Version
    from conda_lock._vendor.poetry.core.constraints.version.version_range import VersionRange
    from conda_lock._vendor.poetry.core.constraints.version.version_union import VersionUnion
    from conda_lock._vendor.poetry.core.version.pep440.segments import Release

    def canon_max(v: Version) -> Version:
        return _canonical_strict_max(v, is_marker_constraint=is_marker_constraint)

    m = re.match(r"(?i)^v?[xX*](\.[xX*])*$", constraint)
    if m:
        return VersionRange()

    # Tilde range
    m = TILDE_CONSTRAINT.match(constraint)
    if m:
        try:
            version = Version.parse(m.group("version"))
        except InvalidVersionError as e:
            raise ParseConstraintError(
                f"Could not parse version constraint: {constraint}"
            ) from e

        high = version.stable.next_minor()
        if version.release.precision == 1:
            high = version.stable.next_major()

        return VersionRange(version, canon_max(high), include_min=True)

    # PEP 440 Tilde range (~=)
    m = TILDE_PEP440_CONSTRAINT.match(constraint)
    if m:
        try:
            version = Version.parse(m.group("version"))
        except InvalidVersionError as e:
            raise ParseConstraintError(
                f"Could not parse version constraint: {constraint}"
            ) from e

        # Per PEP 440, ``~=V.N`` is short for ``>=V.N, ==V.*``, where ``V`` is
        # the release with its last segment dropped, e.g. ``~=1.4.5`` becomes
        # ``1.5`` and ``~=0.0.0.5`` becomes ``0.0.1``. That is: bump the
        # second-to-last release segment and zero out the last one.
        parts = list(version.stable.release.to_parts())
        if len(parts) < 2:
            high = version.stable.next_major()
        else:
            parts[-2] += 1
            parts[-1] = 0
            high = version.stable.__class__(
                epoch=version.stable.epoch,
                release=Release.from_parts(*parts),
            )

        return VersionRange(version, canon_max(high), include_min=True)

    # Caret range
    m = CARET_CONSTRAINT.match(constraint)
    if m:
        try:
            version = Version.parse(m.group("version"))
        except InvalidVersionError as e:
            raise ParseConstraintError(
                f"Could not parse version constraint: {constraint}"
            ) from e

        return VersionRange(
            version, canon_max(version.next_breaking()), include_min=True
        )

    # X Range
    m = X_CONSTRAINT.match(constraint)
    if m:
        op = m.group("op")

        try:
            return _make_x_constraint_range(
                version=Version.parse(m.group("version")),
                invert=op == "!=",
                is_marker_constraint=is_marker_constraint,
            )
        except ValueError:
            raise ValueError(f"Could not parse version constraint: {constraint}")

    # Basic comparator
    m = BASIC_CONSTRAINT.match(constraint)
    if m:
        op = m.group("op")
        version_string = m.group("version")

        if version_string == "dev":
            version_string = "0.0-dev"

        try:
            version = Version.parse(version_string)
        except InvalidVersionError as e:
            raise ParseConstraintError(
                f"Could not parse version constraint: {constraint}"
            ) from e

        if op == "<":
            return VersionRange(max=canon_max(version))
        if op == "<=":
            return VersionRange(max=version, include_max=True)
        if op == ">":
            return VersionRange(min=version)
        if op == ">=":
            return VersionRange(min=version, include_min=True)

        if m.group("wildcard") is not None:
            return _make_x_constraint_range(
                version=version,
                invert=op == "!=",
                is_marker_constraint=is_marker_constraint,
            )

        if op == "!=":
            # PEP 440 strict equality: ``!=V`` excludes exactly ``V``, *not* its
            # prereleases (unlike the ordered ``<V`` comparison).  We therefore
            # leave the upper piece raw; ``2.0.dev1`` correctly matches ``!=2``.
            return VersionUnion(
                VersionRange(max=version),
                VersionRange(min=version),
            )

        return version

    # These below should be reserved for comparing non python packages such as OS
    # versions using `platform_release`
    if not pep440 and (m := BASIC_RELEASE_CONSTRAINT.match(constraint)):
        op = m.group("op")
        release_string = m.group("release")
        build = m.group("build")

        try:
            version = Version(
                release=Version.parse(release_string).release,
                local=build,
            )
        except InvalidVersionError as e:
            raise ParseConstraintError(
                f"Could not parse version constraint: {constraint}"
            ) from e

        if op == "<":
            return VersionRange(max=canon_max(version))
        if op == "<=":
            return VersionRange(max=version, include_max=True)
        if op == ">":
            return VersionRange(min=version)
        if op == ">=":
            return VersionRange(min=version, include_min=True)
        if op == "!=":
            # See PEP 440 ``!=V`` note above (basic constraint branch).
            return VersionUnion(
                VersionRange(max=version),
                VersionRange(min=version),
            )
        return version

    raise ParseConstraintError(f"Could not parse version constraint: {constraint}")


def _make_x_constraint_range(
    version: Version, *, invert: bool = False, is_marker_constraint: bool = False
) -> VersionConstraint:
    from conda_lock._vendor.poetry.core.constraints.version.version_range import VersionRange

    if version.is_postrelease():
        _next = version.next_postrelease()
    elif version.is_stable():
        _next = version.next_stable()
    elif version.is_prerelease():
        _next = version.next_prerelease()
    elif version.is_devrelease():
        _next = version.next_devrelease()
    else:
        raise RuntimeError("version is neither stable, nor pre-release nor dev-release")

    _min = version
    _max = _next

    if not is_marker_constraint:
        _min = _min.first_devrelease()
        if not _max.is_devrelease():
            _max = _max.first_devrelease()

    result = VersionRange(_min, _max, include_min=True)

    if invert:
        return VersionRange().difference(result)

    return result
