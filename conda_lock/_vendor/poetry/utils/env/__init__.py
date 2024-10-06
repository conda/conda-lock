from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.core.utils.helpers import temporary_directory

from conda_lock._vendor.poetry.utils.env.base_env import Env
from conda_lock._vendor.poetry.utils.env.env_manager import EnvManager
from conda_lock._vendor.poetry.utils.env.exceptions import EnvCommandError
from conda_lock._vendor.poetry.utils.env.exceptions import EnvError
from conda_lock._vendor.poetry.utils.env.exceptions import IncorrectEnvError
from conda_lock._vendor.poetry.utils.env.exceptions import InvalidCurrentPythonVersionError
from conda_lock._vendor.poetry.utils.env.exceptions import NoCompatiblePythonVersionFound
from conda_lock._vendor.poetry.utils.env.exceptions import PythonVersionNotFound
from conda_lock._vendor.poetry.utils.env.generic_env import GenericEnv
from conda_lock._vendor.poetry.utils.env.mock_env import MockEnv
from conda_lock._vendor.poetry.utils.env.null_env import NullEnv
from conda_lock._vendor.poetry.utils.env.script_strings import GET_BASE_PREFIX
from conda_lock._vendor.poetry.utils.env.script_strings import GET_ENV_PATH_ONELINER
from conda_lock._vendor.poetry.utils.env.script_strings import GET_ENVIRONMENT_INFO
from conda_lock._vendor.poetry.utils.env.script_strings import GET_PATHS
from conda_lock._vendor.poetry.utils.env.script_strings import GET_PATHS_FOR_GENERIC_ENVS
from conda_lock._vendor.poetry.utils.env.script_strings import GET_PYTHON_VERSION
from conda_lock._vendor.poetry.utils.env.script_strings import GET_PYTHON_VERSION_ONELINER
from conda_lock._vendor.poetry.utils.env.script_strings import GET_SYS_PATH
from conda_lock._vendor.poetry.utils.env.script_strings import GET_SYS_TAGS
from conda_lock._vendor.poetry.utils.env.site_packages import SitePackages
from conda_lock._vendor.poetry.utils.env.system_env import SystemEnv
from conda_lock._vendor.poetry.utils.env.virtual_env import VirtualEnv


if TYPE_CHECKING:
    from collections.abc import Iterator

    from conda_lock._vendor.cleo.io.io import IO
    from conda_lock._vendor.poetry.core.poetry import Poetry as CorePoetry


@contextmanager
def ephemeral_environment(
    executable: Path | None = None,
    flags: dict[str, str | bool] | None = None,
) -> Iterator[VirtualEnv]:
    with temporary_directory() as tmp_dir:
        # TODO: cache PEP 517 build environment corresponding to each project venv
        venv_dir = Path(tmp_dir) / ".venv"
        EnvManager.build_venv(
            path=venv_dir,
            executable=executable,
            flags=flags,
        )
        yield VirtualEnv(venv_dir, venv_dir)


@contextmanager
def build_environment(
    poetry: CorePoetry, env: Env | None = None, io: IO | None = None
) -> Iterator[Env]:
    """
    If a build script is specified for the project, there could be additional build
    time dependencies, eg: cython, setuptools etc. In these cases, we create an
    ephemeral build environment with all requirements specified under
    `build-system.requires` and return this. Otherwise, the given default project
    environment is returned.
    """
    if not env or poetry.package.build_script:
        with ephemeral_environment(executable=env.python if env else None) as venv:
            if io:
                requires = [
                    f"<c1>{requirement}</c1>"
                    for requirement in poetry.pyproject.build_system.requires
                ]

                io.write_error_line(
                    "<b>Preparing</b> build environment with build-system requirements"
                    f" {', '.join(requires)}"
                )

            output = venv.run_pip(
                "install",
                "--disable-pip-version-check",
                "--ignore-installed",
                "--no-input",
                *poetry.pyproject.build_system.requires,
            )

            if io and io.is_debug() and output:
                io.write_error(output)

            yield venv
    else:
        yield env


__all__ = [
    "GET_BASE_PREFIX",
    "GET_ENVIRONMENT_INFO",
    "GET_PATHS",
    "GET_PYTHON_VERSION",
    "GET_SYS_PATH",
    "GET_SYS_TAGS",
    "GET_ENV_PATH_ONELINER",
    "GET_PYTHON_VERSION_ONELINER",
    "GET_PATHS_FOR_GENERIC_ENVS",
    "EnvError",
    "EnvCommandError",
    "IncorrectEnvError",
    "InvalidCurrentPythonVersionError",
    "NoCompatiblePythonVersionFound",
    "PythonVersionNotFound",
    "Env",
    "EnvManager",
    "GenericEnv",
    "MockEnv",
    "NullEnv",
    "SystemEnv",
    "VirtualEnv",
    "SitePackages",
    "build_environment",
    "ephemeral_environment",
]
