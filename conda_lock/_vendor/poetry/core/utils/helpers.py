from __future__ import annotations

import unicodedata

from pathlib import Path

from packaging.utils import canonicalize_name


def combine_unicode(string: str) -> str:
    return unicodedata.normalize("NFC", string)


def module_name(name: str) -> str:
    return canonicalize_name(name).replace("-", "_")


def parse_requires(requires: str) -> list[str]:
    lines = requires.split("\n")

    requires_dist = []
    current_marker = None
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("["):
            # extras or conditional dependencies
            marker = line.lstrip("[").rstrip("]")
            if ":" not in marker:
                extra, marker = marker, ""
            else:
                extra, marker = marker.split(":")

            if extra:
                if marker:
                    marker = f'{marker} and extra == "{extra}"'
                else:
                    marker = f'extra == "{extra}"'

            if marker:
                current_marker = marker

            continue

        if current_marker:
            line = f"{line} ; {current_marker}"

        requires_dist.append(line)

    return requires_dist


def readme_content_type(path: str | Path) -> str:
    suffix = Path(path).suffix
    if suffix == ".rst":
        return "text/x-rst"
    elif suffix in (".md", ".markdown"):
        return "text/markdown"
    else:
        return "text/plain"
