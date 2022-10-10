from typing import TYPE_CHECKING
from typing import Any
from typing import Union

from tomlkit.exceptions import TOMLKitError
from tomlkit.toml_file import TOMLFile as BaseTOMLFile

from conda_lock._vendor.poetry.core.toml import TOMLError
from conda_lock._vendor.poetry.core.utils._compat import Path


if TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument  # noqa


class TOMLFile(BaseTOMLFile):
    def __init__(self, path):  # type: (Union[str, Path]) -> None
        if isinstance(path, str):
            path = Path(path)
        super(TOMLFile, self).__init__(path.as_posix())
        self.__path = path

    @property
    def path(self):  # type: () -> Path
        return self.__path

    def exists(self):  # type: () -> bool
        return self.__path.exists()

    def read(self):  # type: () -> "TOMLDocument"
        try:
            return super(TOMLFile, self).read()
        except (ValueError, TOMLKitError) as e:
            raise TOMLError("Invalid TOML file {}: {}".format(self.path.as_posix(), e))

    def __getattr__(self, item):  # type: (str) -> Any
        return getattr(self.__path, item)

    def __str__(self):  # type: () -> str
        return self.__path.as_posix()
