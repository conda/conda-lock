import re

from typing import Iterator


def filter_platform_selectors(content: str, platform) -> Iterator[str]:
    """ """
    # we support a very limited set of selectors that adhere to platform only
    platform_sel = {
        "linux-64": {"linux64", "unix", "linux"},
        "linux-aarch64": {"aarch64", "unix", "linux"},
        "linux-ppc64le": {"ppc64le", "unix", "linux"},
        "osx-64": {"osx", "osx64", "unix"},
        "osx-arm64": {"arm64", "osx", "unix"},
        "win-64": {"win", "win64"},
    }

    # This code is adapted from conda-build
    sel_pat = re.compile(r"(.+?)\s*(#.*)?\[([^\[\]]+)\](?(2)[^\(\)]*)$")
    for line in content.splitlines(keepends=False):
        if line.lstrip().startswith("#"):
            continue
        m = sel_pat.match(line)
        if m:
            cond = m.group(3)
            if cond in platform_sel[platform]:
                yield line
        else:
            yield line
