from typing import Any
from typing import Optional
from typing import Union

from tomlkit.container import Container
from tomlkit.toml_document import TOMLDocument

from conda_lock._vendor.poetry.core.pyproject.exceptions import PyProjectException
from conda_lock._vendor.poetry.core.pyproject.tables import BuildSystem
from conda_lock._vendor.poetry.core.toml import TOMLFile
from conda_lock._vendor.poetry.core.utils._compat import Path


class PyProjectTOML:
    def __init__(self, path):  # type: (Union[str, Path]) -> None
        self._file = TOMLFile(path=path)
        self._data = None  # type: Optional[TOMLDocument]
        self._build_system = None  # type: Optional[BuildSystem]
        self._poetry_config = None  # type: Optional[TOMLDocument]

    @property
    def file(self):  # type: () -> TOMLFile
        return self._file

    @property
    def data(self):  # type: () -> TOMLDocument
        if self._data is None:
            if not self._file.exists():
                self._data = TOMLDocument()
            else:
                self._data = self._file.read()
        return self._data

    @property
    def build_system(self):  # type: () -> BuildSystem
        if self._build_system is None:
            build_backend = None
            requires = None

            if not self._file.exists():
                build_backend = "poetry.core.masonry.api"
                requires = ["poetry-core"]

            container = self.data.get("build-system", {})
            self._build_system = BuildSystem(
                build_backend=container.get("build-backend", build_backend),
                requires=container.get("requires", requires),
            )
        return self._build_system

    @property
    def poetry_config(self):  # type: () -> Optional[TOMLDocument]
        if self._poetry_config is None:
            self._poetry_config = self.data.get("tool", {}).get("poetry")
            if self._poetry_config is None:
                raise PyProjectException(
                    "[tool.poetry] section not found in {}".format(self._file)
                )
        return self._poetry_config

    def is_poetry_project(self):  # type: () -> bool
        if self.file.exists():
            try:
                _ = self.poetry_config
                return True
            except PyProjectException:
                pass
        return False

    def __getattr__(self, item):  # type: (str) -> Any
        return getattr(self.data, item)

    def save(self):  # type: () -> None
        data = self.data

        if self._poetry_config is not None:
            data["tool"]["poetry"] = self._poetry_config

        if self._build_system is not None:
            if "build-system" not in data:
                data["build-system"] = Container()
            data["build-system"]["requires"] = self._build_system.requires
            data["build-system"]["build-backend"] = self._build_system.build_backend

        self.file.write(data=data)

    def reload(self):  # type: () -> None
        self._data = None
        self._build_system = None
        self._poetry_config = None
