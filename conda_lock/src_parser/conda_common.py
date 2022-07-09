from typing import Optional

from ..src_parser import VersionedDependency
from ..vendor.conda.models.channel import Channel


def conda_spec_to_versioned_dep(spec: str, category: str) -> VersionedDependency:
    from ..vendor.conda.models.match_spec import MatchSpec

    try:
        ms = MatchSpec(spec)
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
        optional=category != "main",
        category=category,
        extras=[],
        build=ms.get("build"),
        conda_channel=channel_str,
    )
