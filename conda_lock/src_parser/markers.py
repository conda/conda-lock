"""Evaluate PEP 508 environment markers.

Environment markers are expressions such as `sys_platform == 'darwin'` that can
be attached to dependency specifications.

<https://www.python.org/dev/peps/pep-0508/#environment-markers>
"""

from typing import Set, Union

from conda_lock.common import warn
from conda_lock.interfaces.vendored_poetry_markers import (
    AnyMarker,
    BaseMarker,
    EmptyMarker,
    MarkerUnion,
    MultiMarker,
    SingleMarker,
    parse_marker,
)
from conda_lock.pypi_solver import PlatformEnv


def get_names(marker: Union[BaseMarker, str]) -> Set[str]:
    """Extract all environment marker names from a marker expression.

    >>> names = get_names(
    ...     "python_version < '3.9' and os_name == 'nt' or os_name == 'posix'"
    ... )
    >>> sorted(names)
    ['os_name', 'python_version']
    """
    if isinstance(marker, str):
        marker = parse_marker(marker)
    if isinstance(marker, SingleMarker):
        return {marker.name}
    if isinstance(marker, (MarkerUnion, MultiMarker)):
        return set.union(*[get_names(m) for m in marker.markers])
    if isinstance(marker, (AnyMarker, EmptyMarker)):
        return set()
    raise NotImplementedError(f"Unknown marker type: {marker!r}")


def evaluate_marker(marker: Union[BaseMarker, str, None], platform: str) -> bool:
    """Evaluate a marker expression for a given platform.

    This is intended to be used for parsing lock specifications, before the Python
    version is known, so markers like `python_version` are not supported.
    If the marker contains any unsupported names, a warning is issued, and the
    corresponding clause will evaluate to `True`.

    >>> evaluate_marker("sys_platform == 'darwin'", "osx-arm64")
    True
    >>> evaluate_marker("sys_platform == 'darwin'", "linux-64")
    False
    >>> evaluate_marker(None, "win-64")
    True

    # Unsupported names evaluate to True
    >>> evaluate_marker("python_version < '0' and implementation_name == 'q'", "win-64")
    True
    """
    if marker is None:
        return True
    if isinstance(marker, str):
        marker = parse_marker(marker)
    env = PlatformEnv(platform=platform)
    marker_env = env.get_marker_env()
    names = get_names(marker)
    supported_names = set(marker_env.keys())
    if not names <= supported_names:
        warn(
            f"Marker '{marker}' contains environment markers: "
            f"{names - supported_names}. Only {supported_names} are supported."
        )
    return marker.validate(marker_env)
