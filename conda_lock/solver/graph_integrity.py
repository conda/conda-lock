"""Check supplied dependency edges, package constraints and category assignment."""

import logging

from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlsplit

from conda_lock.errors import MetadataConsistencyError
from conda_lock.interfaces.vendored_conda import InvalidSpec, MatchSpec
from conda_lock.lockfile.v2prelim.models import LockedDependency


def parse_dependency_spec(dependency: str) -> MatchSpec:
    """Give malformed required metadata the same recovery path at every boundary."""
    try:
        return MatchSpec(dependency)
    except InvalidSpec as exc:
        raise MetadataConsistencyError(
            f"Cannot parse required dependency {dependency!r}",
            recovery=(
                "Report the malformed dependency to the channel maintainer, or "
                "select a package build with valid dependency metadata. "
                "Required dependencies cannot safely be ignored."
            ),
        ) from exc


def check_dependencies_present(planned: Mapping[str, LockedDependency]) -> None:
    missing = {
        name: sorted(
            dep
            for dep in package.dependencies
            if dep not in planned and not dep.startswith("__")
        )
        for name, package in planned.items()
    }
    missing = {
        name: dependencies for name, dependencies in missing.items() if dependencies
    }
    if missing:
        raise MetadataConsistencyError(
            f"Solver plan is missing required packages: {missing}", report_bug=True
        )


def check_categories_assigned(planned: Mapping[str, LockedDependency]) -> None:
    orphans = sorted(
        name
        for name, package in planned.items()
        if not package.categories and not name.startswith("__")
    )
    if orphans:
        raise MetadataConsistencyError(
            f"Planned packages have no category and would be omitted from the lockfile: {orphans}",
            recovery=(
                "Packages outside the source specification may have been added by "
                "solver configuration, such as create_default_packages. Check the "
                "selected solver's configuration and remove unintended additions."
            ),
            report_bug=True,
        )


def _constraint_violations(
    records: Sequence[Mapping[str, Any]], *, include_missing: bool = False
) -> Iterator[tuple[str, str]]:
    """Yield affected dependency names and explanations from raw MatchSpecs."""
    selected = {}
    for record in records:
        filename = record.get("fn") or unquote(
            PurePosixPath(urlsplit(record["url"]).path).name
        )
        build = (
            filename.removesuffix(".tar.bz2").removesuffix(".conda").rsplit("-", 1)[-1]
        )
        selected[record["name"]] = SimpleNamespace(**{**record, "build": build})
    for record in records:
        for field, constraint in [
            (field, constraint)
            for field in ("depends", "constrains")
            for constraint in (record.get(field) or [])
        ]:
            try:
                spec = parse_dependency_spec(constraint)
            except MetadataConsistencyError:
                if field == "depends":
                    raise
                logging.getLogger(__name__).warning(
                    "Ignoring malformed optional constraint %r from %s, "
                    "as Conda solvers do; report it to the channel maintainer.",
                    constraint,
                    record["name"],
                )
                continue
            name: str = spec.name
            if spec.name.startswith("__"):
                continue
            if spec.name not in selected:
                if include_missing and field == "depends":
                    yield name, f"{record['name']} requires missing {constraint}"
                continue  # Optional constrains do not require a package's presence.
            try:
                matches = spec.match(selected[spec.name])
            except (AttributeError, TypeError) as exc:
                raise MetadataConsistencyError(
                    f"Cannot verify {record['name']}'s requirement {constraint}",
                    report_bug=True,
                ) from exc
            if not matches:
                yield (
                    name,
                    (
                        f"Selected {spec.name} does not satisfy {record['name']}'s requirement {constraint}"
                    ),
                )


def packages_requiring_repair(records: Sequence[Mapping[str, Any]]) -> set[str]:
    """Find missing/incompatible packages before constructing an update prefix."""
    return {name for name, _ in _constraint_violations(records, include_missing=True)}


def check_dependency_constraints(records: Sequence[Mapping[str, Any]]) -> None:
    """Check raw version/build requirements before reducing them to lockfile edges."""
    for _, explanation in _constraint_violations(records):
        raise MetadataConsistencyError(explanation, report_bug=True)
