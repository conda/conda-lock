from typing import Optional

from .._vendor.conda.models.channel import Channel
from .._vendor.conda.models.match_spec import MatchSpec
from .._vendor.conda.models.version import treeify, untreeify
from ..src_parser import SourceDependency, VersionedDependency


def conda_spec_to_versioned_dep(spec: str, category: str) -> SourceDependency:
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
        optional=category != "main",
        category=category,
        extras=[],
        build=ms.get("build"),
        conda_channel=channel_str,
    ).to_source()


def merge_version_specs(ver_a: str, ver_b: str) -> str:
    """Merge / And 2 Conda VersionSpec Strings Together"""
    if ver_a == ver_b:
        return ver_a

    # Conda has tools for parsing VersionSpec into a tree format
    ver_a_tree = treeify(ver_a)
    ver_b_tree = treeify(ver_b)

    if (
        isinstance(ver_a_tree, tuple)
        and isinstance(ver_b_tree, tuple)
        and ver_a_tree[0] == ver_b_tree[0] == ","
    ):
        return untreeify((",", *ver_a_tree, *ver_b_tree))
    else:
        return untreeify((",", ver_a_tree, ver_b_tree))
