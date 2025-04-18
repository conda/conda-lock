from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from conda_lock._vendor.poetry.core.poetry import Poetry as BasePoetry

from conda_lock._vendor.poetry.__version__ import __version__
from conda_lock._vendor.poetry.config.source import Source
from conda_lock._vendor.poetry.pyproject.toml import PyProjectTOML


if TYPE_CHECKING:
    from pathlib import Path

    from conda_lock._vendor.poetry.core.packages.project_package import ProjectPackage

    from conda_lock._vendor.poetry.config.config import Config
    from conda_lock._vendor.poetry.packages.locker import Locker
    from conda_lock._vendor.poetry.plugins.plugin_manager import PluginManager
    from conda_lock._vendor.poetry.repositories.repository_pool import RepositoryPool
    from conda_lock._vendor.poetry.toml import TOMLFile


class Poetry(BasePoetry):
    VERSION = __version__

    def __init__(
        self,
        file: Path,
        local_config: dict[str, Any],
        package: ProjectPackage,
        locker: Locker,
        config: Config,
        disable_cache: bool = False,
    ) -> None:
        from conda_lock._vendor.poetry.repositories.repository_pool import RepositoryPool

        super().__init__(file, local_config, package, pyproject_type=PyProjectTOML)

        self._locker = locker
        self._config = config
        self._pool = RepositoryPool(config=config)
        self._plugin_manager: PluginManager | None = None
        self._disable_cache = disable_cache

    @property
    def pyproject(self) -> PyProjectTOML:
        pyproject = super().pyproject
        return cast("PyProjectTOML", pyproject)

    @property
    def file(self) -> TOMLFile:
        return self.pyproject.file

    @property
    def locker(self) -> Locker:
        return self._locker

    @property
    def pool(self) -> RepositoryPool:
        return self._pool

    @property
    def config(self) -> Config:
        return self._config

    @property
    def disable_cache(self) -> bool:
        return self._disable_cache

    def set_locker(self, locker: Locker) -> Poetry:
        self._locker = locker

        return self

    def set_pool(self, pool: RepositoryPool) -> Poetry:
        self._pool = pool

        return self

    def set_config(self, config: Config) -> Poetry:
        self._config = config

        return self

    def get_sources(self) -> list[Source]:
        return [
            Source(**source)
            for source in self.pyproject.data.get("tool", {})
            .get("poetry", {})
            .get("source", [])
        ]
