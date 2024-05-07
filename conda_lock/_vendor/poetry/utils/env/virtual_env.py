from __future__ import annotations

import json
import os
import re
import sys

from contextlib import contextmanager
from copy import deepcopy
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from packaging.tags import Tag

from conda_lock._vendor.poetry.utils.env.base_env import Env
from conda_lock._vendor.poetry.utils.env.script_strings import GET_BASE_PREFIX
from conda_lock._vendor.poetry.utils.env.script_strings import GET_ENVIRONMENT_INFO
from conda_lock._vendor.poetry.utils.env.script_strings import GET_PATHS
from conda_lock._vendor.poetry.utils.env.script_strings import GET_PYTHON_VERSION
from conda_lock._vendor.poetry.utils.env.script_strings import GET_SYS_PATH
from conda_lock._vendor.poetry.utils.env.script_strings import GET_SYS_TAGS
from conda_lock._vendor.poetry.utils.env.system_env import SystemEnv


if TYPE_CHECKING:
    from collections.abc import Iterator


class VirtualEnv(Env):
    """
    A virtual Python environment.
    """

    def __init__(self, path: Path, base: Path | None = None) -> None:
        super().__init__(path, base)

        # If base is None, it probably means this is
        # a virtualenv created from VIRTUAL_ENV.
        # In this case we need to get sys.base_prefix
        # from inside the virtualenv.
        if base is None:
            output = self.run_python_script(GET_BASE_PREFIX)
            self._base = Path(output.strip())

    @property
    def sys_path(self) -> list[str]:
        output = self.run_python_script(GET_SYS_PATH)
        paths: list[str] = json.loads(output)
        return paths

    def get_version_info(self) -> tuple[Any, ...]:
        output = self.run_python_script(GET_PYTHON_VERSION)
        assert isinstance(output, str)

        return tuple(int(s) for s in output.strip().split("."))

    def get_python_implementation(self) -> str:
        implementation: str = self.marker_env["platform_python_implementation"]
        return implementation

    def get_supported_tags(self) -> list[Tag]:
        output = self.run_python_script(GET_SYS_TAGS)

        return [Tag(*t) for t in json.loads(output)]

    def get_marker_env(self) -> dict[str, Any]:
        output = self.run_python_script(GET_ENVIRONMENT_INFO)

        env: dict[str, Any] = json.loads(output)
        return env

    def get_paths(self) -> dict[str, str]:
        output = self.run_python_script(GET_PATHS)
        paths: dict[str, str] = json.loads(output)
        return paths

    def is_venv(self) -> bool:
        return True

    def is_sane(self) -> bool:
        # A virtualenv is considered sane if "python" exists.
        return os.path.exists(self.python)

    def _run(self, cmd: list[str], **kwargs: Any) -> str:
        kwargs["env"] = self.get_temp_environ(environ=kwargs.get("env"))
        return super()._run(cmd, **kwargs)

    def get_temp_environ(
        self,
        environ: dict[str, str] | None = None,
        exclude: list[str] | None = None,
        **kwargs: str,
    ) -> dict[str, str]:
        exclude = exclude or []
        exclude.extend(["PYTHONHOME", "__PYVENV_LAUNCHER__"])

        if environ:
            environ = deepcopy(environ)
            for key in exclude:
                environ.pop(key, None)
        else:
            environ = {k: v for k, v in os.environ.items() if k not in exclude}

        environ.update(kwargs)

        environ["PATH"] = self._updated_path()
        environ["VIRTUAL_ENV"] = str(self._path)

        return environ

    def execute(self, bin: str, *args: str, **kwargs: Any) -> int:
        kwargs["env"] = self.get_temp_environ(environ=kwargs.get("env"))
        return super().execute(bin, *args, **kwargs)

    @contextmanager
    def temp_environ(self) -> Iterator[None]:
        environ = dict(os.environ)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(environ)

    def _updated_path(self) -> str:
        return os.pathsep.join([str(self._bin_dir), os.environ.get("PATH", "")])

    @cached_property
    def includes_system_site_packages(self) -> bool:
        pyvenv_cfg = self._path / "pyvenv.cfg"
        return pyvenv_cfg.exists() and (
            re.search(
                r"^\s*include-system-site-packages\s*=\s*true\s*$",
                pyvenv_cfg.read_text(),
                re.IGNORECASE | re.MULTILINE,
            )
            is not None
        )

    def is_path_relative_to_lib(self, path: Path) -> bool:
        return super().is_path_relative_to_lib(path) or (
            self.includes_system_site_packages
            and SystemEnv(Path(sys.prefix)).is_path_relative_to_lib(path)
        )
