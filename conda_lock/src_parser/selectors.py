import logging
import re

from typing import Iterator, Optional


logger = logging.getLogger(__name__)


def filter_platform_selectors(
    content: str, platform: Optional[str] = None
) -> Iterator[str]:
    """ """
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

    # This code is adapted from conda-build
    sel_pat = re.compile(r"(.+?)\s*(#.*)\[([^\[\]]+)\](?(2)[^\(\)]*)$")
    for line in content.splitlines(keepends=False):
        if line.lstrip().startswith("#"):
            continue
        m = sel_pat.match(line)
        if platform and m:
            cond = m.group(3)
            if cond in platform_sel[platform]:
                yield line
            else:
                logger.warning(
                    f"filtered out line `{line}` on platform {platform} due to "
                    f"non-matching selector `{cond}`"
                )
        else:
            yield line
