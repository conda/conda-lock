from typing import Optional

from conda_lock.models.lock_spec import VersionedDependency

from .._vendor.conda.models.channel import Channel
from .._vendor.conda.models.match_spec import MatchSpec


def conda_spec_to_versioned_dep(spec: str, category: str) -> VersionedDependency:
    """Convert a string form conda spec into a versioned dependency for a given category.

    This is used by the environment.yaml and meta.yaml specification parser
    """

    try:
        ms = MatchSpec(spec)  # type: ignore # This is done in the metaclass for the matchspec
    except Exception as e:
        raise RuntimeError(f"Failed to turn `{spec}` into a MatchSpec") from e

    package_channel: Optional[Channel] = ms.get("channel")
    if package_channel:
        channel_str = package_channel.canonical_name
    else:
        channel_str = None
    return VersionedDependency(
        name=ms.name,
        version=ms.get("version", ""),
        manager="conda",
        category=category,
        extras=[],
        build=ms.get("build"),
        conda_channel=channel_str,
    )
