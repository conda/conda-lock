import os

from .utils._compat import Path
from .utils.appdirs import user_cache_dir
from .utils.appdirs import user_config_dir
from .utils.appdirs import user_data_dir


CACHE_DIR = user_cache_dir("pypoetry-conda-lock")
CONFIG_DIR = user_config_dir("pypoetry-conda-lock")

REPOSITORY_CACHE_DIR = Path(CACHE_DIR) / "cache" / "repositories"


def data_dir():  # type: () -> Path
    if os.getenv("POETRY_HOME"):
        return Path(os.getenv("POETRY_HOME")).expanduser()

    return Path(user_data_dir("pypoetry-conda-lock", roaming=True))
