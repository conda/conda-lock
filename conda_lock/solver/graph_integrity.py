"""Check required packages and constraints in the supplied solver graph."""

from collections.abc import Mapping, Sequence
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


def check_dependency_constraints(records: Sequence[Mapping[str, Any]]) -> None:
    """Check raw version/build requirements before reducing them to lockfile edges."""
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
            if spec.name.startswith("__") or spec.name not in selected:
                continue  # Missing required packages are checked separately.
            try:
                matches = spec.match(selected[spec.name])
            except (AttributeError, TypeError) as exc:
                raise MetadataConsistencyError(
                    f"Cannot verify {record['name']}'s requirement {constraint}"
                ) from exc
            if not matches:
                raise MetadataConsistencyError(
                    f"Selected {spec.name} does not satisfy {record['name']}'s requirement {constraint}"
                )
