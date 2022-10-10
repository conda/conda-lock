from __future__ import absolute_import
from __future__ import unicode_literals

from typing import TYPE_CHECKING
from typing import Any

from conda_lock._vendor.poetry.core.pyproject import PyProjectTOML
from conda_lock._vendor.poetry.core.utils._compat import Path  # noqa


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.packages import ProjectPackage  # noqa
    from conda_lock._vendor.poetry.core.pyproject.toml import PyProjectTOMLFile  # noqa


class Poetry(object):
    def __init__(
        self, file, local_config, package,
    ):  # type: (Path, dict, "ProjectPackage") -> None
        self._pyproject = PyProjectTOML(file)
        self._package = package
        self._local_config = local_config

    @property
    def pyproject(self):  # type: () -> PyProjectTOML
        return self._pyproject

    @property
    def file(self):  # type: () -> "PyProjectTOMLFile"
        return self._pyproject.file

    @property
    def package(self):  # type: () -> "ProjectPackage"
        return self._package

    @property
    def local_config(self):  # type: () -> dict
        return self._local_config

    def get_project_config(self, config, default=None):  # type: (str, Any) -> Any
        return self._local_config.get("config", {}).get(config, default)
