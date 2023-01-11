from __future__ import annotations

from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.mixology.version_solver import VersionSolver


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.packages.project_package import ProjectPackage

    from conda_lock._vendor.poetry.mixology.result import SolverResult
    from conda_lock._vendor.poetry.puzzle.provider import Provider


def resolve_version(root: ProjectPackage, provider: Provider) -> SolverResult:
    solver = VersionSolver(root, provider)

    return solver.solve()
