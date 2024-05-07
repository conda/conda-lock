from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from packaging.utils import canonicalize_name
from conda_lock._vendor.poetry.core.constraints.version import Version

from conda_lock._vendor.poetry.repositories.abstract_repository import AbstractRepository
from conda_lock._vendor.poetry.repositories.exceptions import PackageNotFound


if TYPE_CHECKING:
    from packaging.utils import NormalizedName
    from conda_lock._vendor.poetry.core.constraints.version import VersionConstraint
    from conda_lock._vendor.poetry.core.packages.dependency import Dependency
    from conda_lock._vendor.poetry.core.packages.package import Package
    from conda_lock._vendor.poetry.core.packages.utils.link import Link


class Repository(AbstractRepository):
    def __init__(self, name: str, packages: list[Package] | None = None) -> None:
        super().__init__(name)
        self._packages: list[Package] = []

        for package in packages or []:
            self.add_package(package)

    @property
    def packages(self) -> list[Package]:
        return self._packages

    def find_packages(self, dependency: Dependency) -> list[Package]:
        packages = []
        ignored_pre_release_packages = []

        constraint = dependency.constraint
        allow_prereleases = dependency.allows_prereleases()
        for package in self._find_packages(dependency.name, constraint):
            if package.yanked and not isinstance(constraint, Version):
                # PEP 592: yanked files are always ignored, unless they are the only
                # file that matches a version specifier that "pins" to an exact
                # version
                continue
            if (
                package.is_prerelease()
                and not allow_prereleases
                and not package.is_direct_origin()
            ):
                ignored_pre_release_packages.append(package)
                continue

            packages.append(package)

        self._log(
            f"{len(packages)} packages found for {dependency.name} {constraint!s}",
            level="debug",
        )

        return packages or ignored_pre_release_packages

    def has_package(self, package: Package) -> bool:
        package_id = package.unique_name
        return any(
            package_id == repo_package.unique_name for repo_package in self.packages
        )

    def add_package(self, package: Package) -> None:
        self._packages.append(package)

    def remove_package(self, package: Package) -> None:
        package_id = package.unique_name

        index = None
        for i, repo_package in enumerate(self.packages):
            if package_id == repo_package.unique_name:
                index = i
                break

        if index is not None:
            del self._packages[index]

    def search(self, query: str) -> list[Package]:
        results: list[Package] = []

        for package in self.packages:
            if query in package.name:
                results.append(package)

        return results

    def _find_packages(
        self, name: NormalizedName, constraint: VersionConstraint
    ) -> list[Package]:
        return [
            package
            for package in self._packages
            if package.name == name and constraint.allows(package.version)
        ]

    def _log(self, msg: str, level: str = "info") -> None:
        logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        getattr(logger, level)(f"<c1>Source ({self.name}):</c1> {msg}")

    def __len__(self) -> int:
        return len(self._packages)

    def find_links_for_package(self, package: Package) -> list[Link]:
        return []

    def package(
        self, name: str, version: Version, extras: list[str] | None = None
    ) -> Package:
        canonicalized_name = canonicalize_name(name)
        for package in self.packages:
            if canonicalized_name == package.name and package.version == version:
                return package

        raise PackageNotFound(f"Package {name} ({version}) not found.")
