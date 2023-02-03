from __future__ import annotations

import re
import tarfile

from collections import defaultdict
from functools import cache, cached_property
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

import pkginfo
import requests
import toml

from migrate_code import get_repo_root
from pydantic import BaseModel


def get_pyproject_toml() -> dict[str, Any]:
    return toml.loads((get_repo_root() / "pyproject.toml").read_text())


def get_vendor_root() -> Path:
    return get_repo_root() / get_pyproject_toml()["tool"]["vendoring"]["destination"]


def get_vendor_namespace() -> Path:
    return get_pyproject_toml()["tool"]["vendoring"]["namespace"]


def get_vendor_txt() -> str:
    file = get_pyproject_toml()["tool"]["vendoring"]["requirements"]
    return (get_repo_root() / file).read_text()


def get_directly_vendored_dependencies() -> dict[str, DependencyData]:
    directly_vendored_dependencies: dict[str, DependencyData] = {}
    for line in get_vendor_txt().splitlines():
        line = line.strip()
        if not ("poetry" in line or "cleo" in line):
            continue
        if line.startswith("#"):
            continue
        if line == "":
            continue
        dep = DependencyData.from_line(line)
        directly_vendored_dependencies[dep.name] = dep
    return directly_vendored_dependencies


class License(BaseModel):
    text: str
    destination_file: Path

    @property
    def is_mit(self) -> bool:
        return normalized_str(get_mit_body()) in normalized_str(self.text)

    @property
    def copyright_lines(self) -> set[str]:
        copyright_lines = {
            line.strip().replace("©", "(c)").rstrip(".")
            for line in self.text.splitlines()
            if line.strip().lower().replace("©", "(c)").startswith("copyright (c)")
        }
        return copyright_lines


class DependencyData(BaseModel):
    name: str
    version: str | None = None
    discovered_licenses: list[License] = []
    directly_vendored: bool

    @classmethod
    def from_line(cls, line: str) -> DependencyData:
        """Read a requirement from a line in vendor.txt"""
        name_and_version = line.split("==", 1)
        name = name_and_version[0].strip()
        if len(name_and_version) == 2:
            version = name_and_version[1].strip()
        else:
            version = None
        return cls(name=name, version=version, directly_vendored=True)

    @property
    def _sdist_obj(self) -> pkginfo.SDist:
        with NamedTemporaryFile() as f:
            bytes_io = self._tarfile_obj.fileobj
            bytes_io = cast(BytesIO, bytes_io)
            bytes_io.seek(0)
            f.write(bytes_io.read())
            sdist = pkginfo.SDist(f.name)
        return sdist

    @cached_property
    def _tarfile_obj(self) -> tarfile.TarFile:
        if self.version is None:
            raise RuntimeError(f"Cannot get tarfile for {self.name} without version.")
        url = (
            f"https://pypi.io/packages/source/{self.name[0]}/{self.name}"
            f"/{self.name}-{self.version}.tar.gz"
        )
        response = requests.get(url, allow_redirects=True)
        response.raise_for_status()
        tarfile_bytes = response.content
        return tarfile.open(fileobj=BytesIO(tarfile_bytes))

    def tarinfo_to_str(self, tarinfo: tarfile.TarInfo) -> str:
        file_handle = self._tarfile_obj.extractfile(tarinfo)
        assert file_handle is not None
        text = file_handle.read().decode()
        return text

    @cached_property
    def _root_license(self) -> License:
        root_license_name = f"{self.name}-{self.version}/LICENSE"
        license_tarinfo = self._tarfile_obj.getmember(root_license_name)
        license_text = self.tarinfo_to_str(license_tarinfo)
        return License(
            text=license_text,
            destination_file=get_vendor_root() / f"{self.name}.LICENSE",
        )

    def search_vendored_dependencies(self) -> dict[str, DependencyData]:
        candidate_licenses = [
            m
            for m in self._tarfile_obj.getmembers()
            if "license" in m.name.lower() or "copying" in m.name.lower()
        ]

        candidate_vendored_files = [
            m.name
            for m in self._tarfile_obj.getmembers()
            if "vendor" in m.name.lower() and not m.name.endswith("/.gitignore")
        ]

        if len(candidate_licenses) == 0:
            raise RuntimeError(
                f"Could not find any license files for {self.name} {self.version}"
            )
        if len(candidate_licenses) == 1:
            # Only the root license could be found.
            assert len(candidate_vendored_files) == 0
            # Everything checks out. Nothing to do.
            return {}

        # Collect info about vendored dependencies.
        # Find and read the vendor.txt file which lists names and versions.
        # Find and read all license files of each vendored dependency.
        vendored_licenses: dict[str, list[License]] = defaultdict(list)
        vendor_txt_str: str | None = None
        for m in self._tarfile_obj.getmembers():
            path_parts = Path(m.name).parts
            # Find vendor.txt
            if path_parts[-1] == "vendor.txt":
                assert vendor_txt_str is None
                vendor_txt_str = self.tarinfo_to_str(m)
            # Find vendored licenses
            if "license" in m.name.lower() or "copying" in m.name.lower():
                destination_file = get_vendor_root() / Path(*path_parts[1:])
                if len(path_parts) == 2:
                    # Top-level license.
                    continue
                if m.name.endswith("/spdx/data/licenses.json"):
                    continue
                if m.name.endswith("/spdx/license.py"):
                    continue
                assert "_vendor" in path_parts
                package_name = path_parts[path_parts.index("_vendor") + 1].split(".")[0]
                license_text = self.tarinfo_to_str(m)
                license = License(text=license_text, destination_file=destination_file)
                vendored_licenses[package_name].append(license)
        assert vendor_txt_str is not None

        # Extract the version numbers from vendor.txt.
        vendored_versions: dict[str, str] = {}
        for line in vendor_txt_str.splitlines():
            if line == "":
                continue
            package_name, version_plus_junk = line.split("==")
            version = version_plus_junk.split(";")[0]
            vendored_versions[package_name] = version

        if self.name == "poetry-core":
            # AFAICT, six is unused, and typing-extensions isn't actually vendored.
            del vendored_licenses["six"]
            del vendored_versions["six"]
            del vendored_versions["typing-extensions"]

        # Verify that we found license content for all remaining items from vendor.txt,
        # and that there are no extra license files.
        assert set(vendored_licenses) == set(vendored_versions), (
            vendored_licenses.keys(),
            vendored_versions.keys(),
        )

        # There are two redundant license files called LICENSE and COPYING.
        # The contents are identical except for the copyright year.
        # Let's ignore COPYING.
        assert len(vendored_licenses["jsonschema"]) == 2
        sorted_jsonschema_licenses = sorted(
            vendored_licenses["jsonschema"],
            key=lambda license: license.destination_file.name,
        )
        assert [
            license.destination_file.name for license in sorted_jsonschema_licenses
        ] == ["COPYING", "LICENSE"]
        assert (
            sorted_jsonschema_licenses[0].text.replace(
                "Copyright (c) 2013 Julian Berman", "Copyright (c) 2012 Julian Berman"
            )
            == sorted_jsonschema_licenses[1].text
        )
        vendored_licenses["jsonschema"] = [sorted_jsonschema_licenses[1]]

        discovered_vendored_dependencies = {
            package_name: DependencyData(
                name=package_name,
                version=vendored_versions[package_name],
                discovered_licenses=vendored_licenses[package_name],
                directly_vendored=False,
            )
            for package_name in vendored_versions
        }
        return discovered_vendored_dependencies

    def license_description(self) -> str:
        if self.name == "packaging":
            license_description = "Apache-2.0 or BSD-2-Clause"
        else:
            assert all(license.is_mit for license in self.discovered_licenses)
            license_description = "MIT"
        return license_description

    def copyright_line(self) -> str:
        package_name = self.name
        licenses = self.discovered_licenses
        # Extract all copyright lines from the various licenses
        copyright_lines = {
            line for license in licenses for line in license.copyright_lines
        }
        # Handle a few exceptional cases
        if package_name == "pyparsing":
            # The copyright line is contained in pyparsing.py.
            assert copyright_lines == set()
            copyright_lines = {"Copyright (c) 2003-2019  Paul T. McGuire"}
        assert len(copyright_lines) == 1, (package_name, copyright_lines)
        copyright_line = copyright_lines.pop()
        return copyright_line

    def describe_short(self) -> str:
        return f"{self.name} v{self.version}, licensed as {self.license_description()}."

    def describe_markdown(self) -> str:
        if len(self.discovered_licenses) == 1:
            license = self.discovered_licenses[0]
            destination_file = license.destination_file
            link = (
                f"[{self.license_description()}]"
                f"({destination_file.relative_to(get_vendor_root())})"
            )
        elif self.name == "packaging":
            assert len(self.discovered_licenses) == 3
            licenses = {
                reference: [
                    license
                    for license in self.discovered_licenses
                    if license.destination_file.name.endswith(reference)
                ][0]
                for reference in ["LICENSE", "APACHE", "BSD"]
            }
            relative_paths = {
                reference: license.destination_file.relative_to(get_vendor_root())
                for reference, license in licenses.items()
            }
            link = (
                f"[one]({relative_paths['LICENSE']}) of "
                f"[Apache-2.0]({relative_paths['APACHE']}) or "
                f"[BSD-2-Clause]({relative_paths['BSD']})"
            )
        else:
            raise NotImplementedError(
                f"Found {len(self.discovered_licenses)} " f"licenses for {self.name}."
            )
        description = (
            f"{self.name} v{self.version}, "
            f"licensed as {link}, {self.copyright_line()}."
        )
        return description


def normalized_str(s: str) -> str:
    no_newlines = s.replace("\n", " ").replace("\r", " ")
    split_spaces = no_newlines.split(" ")
    nonempty = [x for x in split_spaces if x != ""]
    return " ".join(nonempty)


@cache
def get_mit_body() -> str:
    repo_root = get_repo_root()
    full_text = (repo_root / "LICENSE").read_text()
    first_block = full_text.split("\n---\n")[0]
    start = first_block.find("Permission is hereby granted")
    mit_body = first_block[start:].replace("\n", " ").replace("  ", " ").strip()
    return mit_body


class Requirement(BaseModel):
    name: str
    version_requirements: str | None = None
    python_requirements: str | None = None
    sources: list[str]

    def as_requirements_txt_line(self) -> str:
        requirement_line = f"{self.name}"
        if self.version_requirements is not None:
            requirement_line += f" {self.version_requirements}"
        if self.python_requirements is not None:
            requirement_line += f"; {self.python_requirements}"
        requirement_line = "# " + ", ".join(self.sources) + ":\n" + requirement_line
        return requirement_line


def req_to_req_obj(req: str, dep: DependencyData) -> Requirement | None:
    # Typical req: ('cachecontrol[filecache] (>=0.12.9,<0.13.0); '
    #               'python_version >= "3.6" and python_version < "4.0"')

    name_and_rest = req.split(" ", 1)

    # 'cachecontrol[filecache]'
    name = name_and_rest[0]

    if len(name_and_rest) == 1:
        version_requirements = None
        python_requirements = None
    else:
        rest = name_and_rest[1]
        version_and_python = rest.split("; ", 1)

        # '(>=0.12.9,<0.13.0)'
        version_requirements = version_and_python[0]

        if len(version_and_python) == 1:
            python_requirements = None
        else:
            # 'python_version >= "3.6" and python_version < "4.0"'
            python_requirements = version_and_python[1]
    if version_requirements is not None:
        # '>=0.12.9,<0.13.0'
        version_requirements = version_requirements.strip("()")
        if version_requirements == "":
            version_requirements = None
    if python_requirements is None:
        effective_python_requirements = None
    else:
        # Replace "x.y" with "(x, y)", e.g. "3.6" -> "(3, 6)"
        python_requirements = re.sub(
            r"\"(\d+)\.(\d+)\"", r"(\1, \2)", python_requirements
        )

        # '>=3.6'
        conda_lock_requires_python = get_pyproject_toml()["project"]["requires-python"]

        # "", "6"
        empty, min_minor_str = conda_lock_requires_python.split(">=3.")

        assert empty == ""

        # 6
        min_minor = int(min_minor_str)

        # [(3, 6), (3, 7), (3, 8), ... (3, 20)]
        potentially_relevant_python_versions = [
            (3, minor) for minor in range(min_minor, 20)
        ]

        # All the potentially relevant python versions which satisfy the
        # python_version constraints
        relevant_python_versions = [
            python_version
            for python_version in potentially_relevant_python_versions
            if eval(
                python_requirements,
                {"__builtins__": {}},
                {"python_version": python_version},
            )
        ]

        # Skip requirement if isn't compatible with relevant Python versions
        if len(relevant_python_versions) == 0:
            return None

        if relevant_python_versions == potentially_relevant_python_versions:
            # If the requirement is compatible with all relevant Python versions,
            # then we don't need to specify a Python version requirement.
            effective_python_requirements = None
        else:
            # Assume that the requirement is effectively 'python_version <= "3.x"'
            # and compute x.
            if relevant_python_versions == [
                python_version
                for python_version in potentially_relevant_python_versions
                if python_version <= max(relevant_python_versions)
            ]:
                effective_python_requirements = (
                    f'python_version <= "3.{max(relevant_python_versions)[1]}"'
                )
            else:
                raise NotImplementedError(
                    "No effective description for Python requirements "
                    f"{relevant_python_versions}"
                )
    req_obj = Requirement(
        name=name,
        version_requirements=version_requirements,
        python_requirements=effective_python_requirements,
        sources=[dep.name],
    )
    return req_obj


def merge_requirements(
    relevant_requirements: list[Requirement],
) -> dict[str, Requirement]:
    merged_requirements: dict[str, Requirement] = {}
    for req_name in set(req.name for req in relevant_requirements):
        # Simply concatenate the version specifiers.
        merged_specifiers = ",".join(
            req.version_requirements
            for req in relevant_requirements
            if req.name == req_name and req.version_requirements is not None
        )
        # Simplify by hand a few special cases
        if merged_specifiers == ">=0.6.0,<0.7.0,>=0.6.2,<0.7.0":
            merged_specifiers = ">=0.6.2,<0.7.0"
        elif merged_specifiers == ">=1.6.0,<2.0.0,>=1.7.0,<2.0.0":
            merged_specifiers = ">=1.7.0,<2.0.0"

        # Get the set of distinct Python version requirements.
        python_requirements_set = {
            req.python_requirements
            for req in relevant_requirements
            if req.name == req_name
        }
        # The direct dependencies which source this requirement.
        sources = {
            source
            for req in relevant_requirements
            if req.name == req_name
            for source in req.sources
        }
        # In our case, no package has multiple distinct Python version requirements, so
        # we don't need to handle that case.
        if len(python_requirements_set) > 1:
            raise NotImplementedError("Multiple Python requirements")
        python_requirements = python_requirements_set.pop()
        merged_requirements[req_name] = Requirement(
            name=req_name,
            version_requirements=merged_specifiers,
            python_requirements=python_requirements,
            sources=sorted(list(sources)),
        )
    # Sort the merged requirements in alphabetical order
    merged_requirements = dict(sorted(merged_requirements.items()))
    return merged_requirements
