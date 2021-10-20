import pathlib
import re
import sys

from urllib.parse import urlparse

import yaml

from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.selectors import filter_platform_selectors

from . import PipPackage


def parse_explicit_file(
    explicit_file: pathlib.Path,
) -> tuple[list[str], list[PipPackage]]:
    if not explicit_file.exists():
        raise FileNotFoundError(f"{explicit_file} not found")

    conda_urls: list[str] = []
    pip_specs: list[PipPackage] = []

    pip_lines: list[str] = []

    with explicit_file.open("r") as fo:
        for line in fo:
            if line.startswith("# pip "):
                subline = line[6:]
                if pip_lines and pip_lines[-1].endswith("\\"):
                    pip_lines[-1] = pip_lines[-1][:-1] + subline.strip()
                else:
                    pip_lines.append(subline.strip())
            elif line.startswith("http"):
                conda_urls.append(line.strip())
    for line in pip_lines:
        name, spec = line.split(" @ ")
        url = spec.split(" ")[0]
        path = pathlib.Path(urlparse(url).path)
        while path.suffix in {".tar", ".tgz", ".gz", ".bz2", ".whl"}:
            path = path.with_suffix("")
        parts = path.name.split("-")
        hashes = [hash.split("=")[1].replace(":", "=") for hash in spec.split(" ")[1:]]
        version = parts[1] if len(parts) > 1 else None
        pip_specs.append(
            {"name": name, "version": version, "url": url, "hashes": hashes}
        )

    return conda_urls, pip_specs
