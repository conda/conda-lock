"""Check supplied dependency edges, package constraints and category assignment."""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlsplit

from conda_lock.errors import MetadataConsistencyError
from conda_lock.interfaces.vendored_conda import MatchSpec
from conda_lock.lockfile.v2prelim.models import LockedDependency


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
            f"Solver plan is missing required packages: {missing}"
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
        for constraint in [
            *(record.get("depends") or []),
            *(record.get("constrains") or []),
        ]:
            spec = MatchSpec(constraint)
            name: str = spec.name
            if spec.name.startswith("__"):
                continue
            if spec.name not in selected:
                if include_missing and constraint in (record.get("depends") or []):
                    yield name, f"{record['name']} requires missing {constraint}"
                continue  # Optional constrains do not require a package's presence.
            try:
                matches = spec.match(selected[spec.name])
            except (AttributeError, TypeError) as exc:
                raise MetadataConsistencyError(
                    f"Cannot verify {record['name']}'s requirement {constraint}"
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
        raise MetadataConsistencyError(explanation)
