import pathlib
import re
import sys

from urllib.parse import urlparse

import yaml

from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.selectors import filter_platform_selectors


def parse_explicit_file(explicit_file: pathlib.Path) -> tuple[list[str], list[str]]:
    if not explicit_file.exists():
        raise FileNotFoundError(f"{explicit_file} not found")

    specs: list[str] = []
    pip_specs: list[str] = []

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
                path = pathlib.Path(urlparse(line).path)
                while path.suffix in {".tar", ".tgz", ".gz", ".bz2"}:
                    path = path.with_suffix("")
                parts = path.name.split("-")[:-1]
                version = parts.pop()
                name = "-".join(parts)
                specs.append(f"{name} ==={version}")
    for line in pip_lines:
        name, spec = line.split(" @ ")
        url = spec.split(" ")[0]
        path = pathlib.Path(urlparse(url).path)
        while path.suffix in {".tar", ".tgz", ".gz", ".bz2", ".whl"}:
            path = path.with_suffix("")
        parts = path.name.split("-")
        try:
            version = parts[1]
            pip_specs.append(f"{name} ==={version}")
        except IndexError:
            hashes = [
                hash.split("=")[1].replace(":", "=") for hash in spec.split(" ")[1:]
            ]
            pip_specs.append(f"{name} @ {url}#{hashes[0]}")

    return specs, pip_specs
