"""
PEP-517 compliant buildsystem API
"""
import logging

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from conda_lock._vendor.poetry.core.factory import Factory
from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.utils._compat import unicode

from .builders.sdist import SdistBuilder
from .builders.wheel import WheelBuilder


log = logging.getLogger(__name__)


def get_requires_for_build_wheel(
    config_settings=None,
):  # type: (Optional[Dict[str, Any]]) -> List[str]
    """
    Returns an additional list of requirements for building, as PEP508 strings,
    above and beyond those specified in the pyproject.toml file.

    This implementation is optional. At the moment it only returns an empty list, which would be the same as if
    not define. So this is just for completeness for future implementation.
    """

    return []


# For now, we require all dependencies to build either a wheel or an sdist.
get_requires_for_build_sdist = get_requires_for_build_wheel


def prepare_metadata_for_build_wheel(
    metadata_directory, config_settings=None
):  # type: (str, Optional[Dict[str, Any]]) -> str
    poetry = Factory().create_poetry(Path(".").resolve(), with_dev=False)
    builder = WheelBuilder(poetry)

    dist_info = Path(metadata_directory, builder.dist_info)
    dist_info.mkdir(parents=True, exist_ok=True)

    if "scripts" in poetry.local_config or "plugins" in poetry.local_config:
        with (dist_info / "entry_points.txt").open("w", encoding="utf-8") as f:
            builder._write_entry_points(f)

    with (dist_info / "WHEEL").open("w", encoding="utf-8") as f:
        builder._write_wheel_file(f)

    with (dist_info / "METADATA").open("w", encoding="utf-8") as f:
        builder._write_metadata_file(f)

    return dist_info.name


def build_wheel(
    wheel_directory, config_settings=None, metadata_directory=None
):  # type: (str, Optional[Dict[str, Any]], Optional[str]) -> str
    """Builds a wheel, places it in wheel_directory"""
    poetry = Factory().create_poetry(Path(".").resolve(), with_dev=False)

    return unicode(WheelBuilder.make_in(poetry, Path(wheel_directory)))


def build_sdist(
    sdist_directory, config_settings=None
):  # type: (str, Optional[Dict[str, Any]]) -> str
    """Builds an sdist, places it in sdist_directory"""
    poetry = Factory().create_poetry(Path(".").resolve(), with_dev=False)

    path = SdistBuilder(poetry).build(Path(sdist_directory))

    return unicode(path.name)


def build_editable(
    wheel_directory, config_settings=None, metadata_directory=None,
):  # type: (str, Optional[Dict[str, Any]], Optional[str]) -> str
    poetry = Factory().create_poetry(Path(".").resolve(), with_dev=False)

    return unicode(WheelBuilder.make_in(poetry, Path(wheel_directory), editable=True))


get_requires_for_build_editable = get_requires_for_build_wheel
prepare_metadata_for_build_editable = prepare_metadata_for_build_wheel
