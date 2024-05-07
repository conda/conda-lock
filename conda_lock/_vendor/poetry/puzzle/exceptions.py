from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.packages.dependency import Dependency
    from conda_lock._vendor.poetry.core.packages.package import Package

    from conda_lock._vendor.poetry.mixology.failure import SolveFailure


class SolverProblemError(Exception):
    def __init__(self, error: SolveFailure) -> None:
        self._error = error

        super().__init__(str(error))

    @property
    def error(self) -> SolveFailure:
        return self._error


class OverrideNeeded(Exception):
    def __init__(self, *overrides: dict[Package, dict[str, Dependency]]) -> None:
        self._overrides = overrides

    @property
    def overrides(self) -> tuple[dict[Package, dict[str, Dependency]], ...]:
        return self._overrides
