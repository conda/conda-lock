from __future__ import annotations

import enum
import warnings

from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.config.config import Config
from conda_lock._vendor.poetry.repositories.abstract_repository import AbstractRepository
from conda_lock._vendor.poetry.repositories.exceptions import PackageNotFound
from conda_lock._vendor.poetry.repositories.repository import Repository
from conda_lock._vendor.poetry.utils.cache import ArtifactCache


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.constraints.version import Version
    from conda_lock._vendor.poetry.core.packages.dependency import Dependency
    from conda_lock._vendor.poetry.core.packages.package import Package

_SENTINEL = object()


class Priority(IntEnum):
    # The order of the members below dictates the actual priority. The first member has
    # top priority.
    DEFAULT = enum.auto()
    PRIMARY = enum.auto()
    SECONDARY = enum.auto()
    SUPPLEMENTAL = enum.auto()
    EXPLICIT = enum.auto()


@dataclass(frozen=True)
class PrioritizedRepository:
    repository: Repository
    priority: Priority


class RepositoryPool(AbstractRepository):
    def __init__(
        self,
        repositories: list[Repository] | None = None,
        ignore_repository_names: object = _SENTINEL,
        *,
        config: Config | None = None,
    ) -> None:
        super().__init__("poetry-repository-pool")
        self._repositories: OrderedDict[str, PrioritizedRepository] = OrderedDict()

        if repositories is None:
            repositories = []
        for repository in repositories:
            self.add_repository(repository)

        self._artifact_cache = ArtifactCache(
            cache_dir=(config or Config.create()).artifacts_cache_directory
        )

        if ignore_repository_names is not _SENTINEL:
            warnings.warn(
                "The 'ignore_repository_names' argument to 'RepositoryPool.__init__' is"
                " deprecated. It has no effect anymore and will be removed in a future"
                " version.",
                DeprecationWarning,
                stacklevel=2,
            )

    @staticmethod
    def from_packages(packages: list[Package], config: Config | None) -> RepositoryPool:
        pool = RepositoryPool(config=config)
        for package in packages:
            if package.is_direct_origin():
                continue

            repo_name = package.source_reference or "PyPI"
            try:
                repo = pool.repository(repo_name)
            except IndexError:
                repo = Repository(repo_name)
                pool.add_repository(repo)

            if not repo.has_package(package):
                repo.add_package(package)

        return pool

    @property
    def repositories(self) -> list[Repository]:
        """
        Returns the repositories in the pool,
        in the order they will be searched for packages.

        ATTENTION: For backwards compatibility and practical reasons,
                   repositories with priority EXPLICIT are NOT included,
                   because they will not be searched.
        """
        sorted_repositories = self._sorted_repositories
        return [
            prio_repo.repository
            for prio_repo in sorted_repositories
            if prio_repo.priority is not Priority.EXPLICIT
        ]

    @property
    def all_repositories(self) -> list[Repository]:
        return [prio_repo.repository for prio_repo in self._sorted_repositories]

    @property
    def _sorted_repositories(self) -> list[PrioritizedRepository]:
        return sorted(
            self._repositories.values(), key=lambda prio_repo: prio_repo.priority
        )

    @property
    def artifact_cache(self) -> ArtifactCache:
        return self._artifact_cache

    def has_default(self) -> bool:
        return self._contains_priority(Priority.DEFAULT)

    def has_primary_repositories(self) -> bool:
        return self._contains_priority(Priority.PRIMARY)

    def _contains_priority(self, priority: Priority) -> bool:
        return any(
            prio_repo.priority is priority for prio_repo in self._repositories.values()
        )

    def has_repository(self, name: str) -> bool:
        return name.lower() in self._repositories

    def repository(self, name: str) -> Repository:
        return self._get_prioritized_repository(name).repository

    def get_priority(self, name: str) -> Priority:
        return self._get_prioritized_repository(name).priority

    def _get_prioritized_repository(self, name: str) -> PrioritizedRepository:
        name = name.lower()
        if self.has_repository(name):
            return self._repositories[name]
        raise IndexError(f'Repository "{name}" does not exist.')

    def add_repository(
        self,
        repository: Repository,
        default: bool = False,
        secondary: bool = False,
        *,
        priority: Priority = Priority.PRIMARY,
    ) -> RepositoryPool:
        """
        Adds a repository to the pool.
        """
        repository_name = repository.name.lower()
        if self.has_repository(repository_name):
            raise ValueError(
                f"A repository with name {repository_name} was already added."
            )

        if default or secondary:
            warnings.warn(
                "Parameters 'default' and 'secondary' to"
                " 'RepositoryPool.add_repository' are deprecated. Please provide"
                " the keyword-argument 'priority' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            priority = Priority.DEFAULT if default else Priority.SECONDARY

        if priority is Priority.DEFAULT and self.has_default():
            raise ValueError("Only one repository can be the default.")

        self._repositories[repository_name] = PrioritizedRepository(
            repository, priority
        )
        return self

    def remove_repository(self, name: str) -> RepositoryPool:
        if not self.has_repository(name):
            raise IndexError(
                f"RepositoryPool can not remove unknown repository '{name}'."
            )
        del self._repositories[name.lower()]
        return self

    def package(
        self,
        name: str,
        version: Version,
        extras: list[str] | None = None,
        repository_name: str | None = None,
    ) -> Package:
        if repository_name:
            return self.repository(repository_name).package(
                name, version, extras=extras
            )

        for repo in self.repositories:
            try:
                return repo.package(name, version, extras=extras)
            except PackageNotFound:
                continue
        raise PackageNotFound(f"Package {name} ({version}) not found.")

    def find_packages(self, dependency: Dependency) -> list[Package]:
        repository_name = dependency.source_name
        if repository_name:
            return self.repository(repository_name).find_packages(dependency)

        packages: list[Package] = []
        for repo in self.repositories:
            if packages and self.get_priority(repo.name) is Priority.SUPPLEMENTAL:
                break
            packages += repo.find_packages(dependency)
        return packages

    def search(self, query: str) -> list[Package]:
        results: list[Package] = []
        for repo in self.repositories:
            results += repo.search(query)
        return results
