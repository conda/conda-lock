import logging
import re

from typing import TYPE_CHECKING, List, Optional


if TYPE_CHECKING:
    from ruamel.yaml.comments import Comment

    from conda_lock.src_parser import SourceDependency


logger = logging.getLogger(__name__)
sel_pat = re.compile(r"(#.*)\[([^\[\]]+)\](?(2)[^\(\)]*)$")


def parse_selector_comment_for_dep(
    yaml_comments: "Comment", dep_idx: int
) -> Optional[List[str]]:
    if dep_idx not in yaml_comments.items:
        return None

    comment: str = yaml_comments.items[dep_idx][0].value
    parsed_comment = comment.partition("\n")[0].rstrip()

    # This code is adapted from conda-build
    m = sel_pat.match(parsed_comment)
    return [m.group(2)] if m else None


def dep_in_platform_selectors(
    source_dep: "SourceDependency",
    platform: str,
) -> bool:
    # we support a very limited set of selectors that adhere to platform only
    # https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html#preprocessing-selectors

    platform_sel = {
        "linux-64": {"linux64", "unix", "linux"},
        "linux-aarch64": {"aarch64", "unix", "linux"},
        "linux-ppc64le": {"ppc64le", "unix", "linux"},
        # "osx64" is a selector unique to conda-build referring to
        # platforms on macOS and the Python architecture is x86-64
        "osx-64": {"osx64", "osx", "unix"},
        "osx-arm64": {"arm64", "osx", "unix"},
        "win-64": {"win", "win64"},
    }

    return platform in platform_sel and (
        source_dep.selectors.platform is None
        or any(
            sel_elem in platform_sel[platform]
            for sel_elem in source_dep.selectors.platform
        )
    )
