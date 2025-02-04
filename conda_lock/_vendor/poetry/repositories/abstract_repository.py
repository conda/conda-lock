from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.constraints.version import Version
    from conda_lock._vendor.poetry.core.packages.dependency import Dependency
    from conda_lock._vendor.poetry.core.packages.package import Package


class AbstractRepository(ABC):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def find_packages(self, dependency: Dependency) -> list[Package]: ...

    @abstractmethod
    def search(self, query: str | list[str]) -> list[Package]: ...

    @abstractmethod
    def package(
        self,
        name: str,
        version: Version,
        extras: list[str] | None = None,
    ) -> Package: ...
