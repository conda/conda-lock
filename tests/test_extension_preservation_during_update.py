"""
Tests for extension-preservation behaviour during `conda-lock --update`.

This file focuses on unit tests for helper functions and will contain
the new integration test that uses a local conda channel fixture.
"""

import io
import json
import tarfile
import zipfile

from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import pytest

from conda_lock.conda_lock import run_lock
from conda_lock.lockfile import parse_conda_lock_file
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.lookup import DEFAULT_MAPPING_URL


@pytest.fixture
def local_conda_channel(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Creates a temporary local conda channel with a mix of .conda and .tar.bz2 packages.
    """
    channel_path = tmp_path / "channel"
    linux_64_path = channel_path / "linux-64"
    noarch_path = channel_path / "noarch"
    linux_64_path.mkdir(parents=True)
    noarch_path.mkdir(parents=True)

    packages = [
        {
            "name": "libgcc-ng",
            "version": "9.3.0",
            "build": "h5101ec6_17",
            "build_number": 17,
            "conda_format": False,
            "depends": [],
            "md5": "ccc6922251f043975762cb348d3b25e7",
            "sha256": "5c929a9a528691a039751797c23f140ecffe258788915ff75e533088b9a11756",
            "platform": "linux-64",
        },
        {
            "name": "tzdata",
            "version": "2022g",
            "build": "h191b570_0",
            "build_number": 0,
            "conda_format": True,
            "depends": [],
            "md5": "2382ac3ccca4632e14b0c4c4be432fba",
            "sha256": "74e464a5ee94c6e7b0b5c5ad32d7ad1e1f1c5e3b52e5f5c6f5c24ed98f7a3f8b",
            "platform": "noarch",
        },
        {
            "name": "zlib",
            "version": "1.2.13",
            "build": "hd590300_5",
            "build_number": 5,
            "conda_format": False,
            "depends": ["libgcc-ng >=9.3.0"],
            "md5": "1234567890abcdef1234567890abcdef",
            "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "platform": "linux-64",
        },
        {
            "name": "ca-certificates",
            "version": "2023.1.10",
            "build": "h06a4308_0",
            "build_number": 0,
            "conda_format": True,
            "depends": [],
            "md5": "fedcba0987654321fedcba0987654321",
            "sha256": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "platform": "linux-64",
        },
        {
            "name": "tzdata",
            "version": "2023a",
            "build": "h71febdd_0",
            "build_number": 0,
            "conda_format": True,
            "depends": [],
            "md5": "newHash123456789abcdef",
            "sha256": "newSha256Hash1234567890abcdef1234567890abcdef1234567890abcdef",
            "platform": "noarch",
        },
    ]

    repodata: dict[str, dict[str, Any]] = {"packages": {}, "packages.conda": {}}

    for pkg in packages:
        extension = ".conda" if pkg["conda_format"] else ".tar.bz2"
        filename = f"{pkg['name']}-{pkg['version']}-{pkg['build']}{extension}"
        subdir_path = noarch_path if pkg["platform"] == "noarch" else linux_64_path
        pkg_file = subdir_path / filename
        pkg_file.touch()

        # Create a dummy archive with an info file
        info_dir = "info"
        info_content = json.dumps(
            {"name": pkg["name"], "version": pkg["version"], "build": pkg["build"]}
        )

        if extension == ".conda":
            with zipfile.ZipFile(pkg_file, "w") as zf:
                zf.writestr(f"{info_dir}/index.json", info_content)
        else:
            with tarfile.open(pkg_file, "w:bz2") as tf:
                tarinfo = tarfile.TarInfo(name=f"{info_dir}/index.json")
                tarinfo.size = len(info_content)
                tf.addfile(tarinfo, fileobj=io.BytesIO(info_content.encode()))

        repo_key = "packages.conda" if pkg["conda_format"] else "packages"
        repodata[repo_key][filename] = {
            "build": pkg["build"],
            "build_number": pkg["build_number"],
            "depends": pkg["depends"],
            "md5": pkg["md5"],
            "sha256": pkg["sha256"],
            "name": pkg["name"],
            "version": pkg["version"],
            "subdir": pkg["platform"],
        }

    for path in [linux_64_path, noarch_path]:
        with open(path / "repodata.json", "w") as f:
            json.dump(repodata, f)

    yield channel_path


def test_extension_format_detection():
    """
    Unit test for detecting package format from URL.
    This is a helper function that the preservation logic will need.
    """

    def detect_package_format(url: str) -> str:
        """Helper function to detect package format from URL."""
        parsed = urlparse(url)
        path = parsed.path
        if path.endswith(".conda"):
            return "conda"
        elif path.endswith(".tar.bz2"):
            return "tar.bz2"
        else:
            return "unknown"

    # Test various URL formats
    conda_url = (
        "https://conda.anaconda.org/conda-forge/noarch/tzdata-2022g-h191b570_0.conda"
    )
    assert detect_package_format(conda_url) == "conda"

    tar_bz2_url = (
        "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.2.13-hd590300_5.tar.bz2"
    )
    assert detect_package_format(tar_bz2_url) == "tar.bz2"

    unknown_url = "https://conda.anaconda.org/conda-forge/linux-64/package-1.0-h123.zip"
    assert detect_package_format(unknown_url) == "unknown"


def test_lockfile_entry_key_generation():
    """
    Unit test for generating unique keys for lockfile entries.
    The preservation logic needs this to match old and new package entries.
    """

    def make_package_key(
        name: str, version: str, build: Optional[str], platform: str
    ) -> tuple[str, str, str, str]:
        """Generate a unique key for a package entry."""
        return (name, version, build or "", platform)

    # Test creating package keys
    pkg1 = LockedDependency(
        name="numpy",
        version="1.21.0",
        build="py39_0",
        platform="linux-64",
        manager="conda",
        dependencies={},
        url="https://example.com/numpy-1.21.0-py39_0.conda",
        hash=HashModel(md5="abc123", sha256="def456"),
        categories={"main"},
    )

    key1 = make_package_key(pkg1.name, pkg1.version, pkg1.build, pkg1.platform)
    expected_key1 = ("numpy", "1.21.0", "py39_0", "linux-64")
    assert key1 == expected_key1

    # Test that different builds create different keys
    pkg2 = LockedDependency(
        name="numpy",
        version="1.21.0",
        build="py38_0",  # Different build
        platform="linux-64",
        manager="conda",
        dependencies={},
        url="https://example.com/numpy-1.21.0-py38_0.conda",
        hash=HashModel(md5="abc123", sha256="def456"),
        categories={"main"},
    )

    key2 = make_package_key(pkg2.name, pkg2.version, pkg2.build, pkg2.platform)
    expected_key2 = ("numpy", "1.21.0", "py38_0", "linux-64")
    assert key2 == expected_key2
    assert key1 != key2  # Different builds should have different keys


def test_update_with_local_channel_preserves_extensions(
    tmp_path: Path, local_conda_channel: Path, monkeypatch: "pytest.MonkeyPatch"
):
    """
    Integration test using a local channel to verify that unchanged packages
    preserve their file extensions (.conda vs .tar.bz2) during an update.
    """
    monkeypatch.chdir(tmp_path)
    channel_url = f"file://{local_conda_channel}"

    # 1. Initial lockfile generation
    initial_env_file = tmp_path / "environment.yml"
    initial_env_content = f"""
name: test-env
channels:
  - {channel_url}
dependencies:
  - tzdata=2022g
  - zlib=1.2.13
  - ca-certificates=2023.1.10
"""
    initial_env_file.write_text(initial_env_content)
    lockfile_path = tmp_path / "conda-lock.yml"

    run_lock(
        [initial_env_file],
        lockfile_path=lockfile_path,
        platforms=["linux-64"],
        conda_exe="mamba",
        mapping_url=DEFAULT_MAPPING_URL,
    )

    # 2. Verify initial state
    original_lock = parse_conda_lock_file(lockfile_path)
    original_packages = {p.name: p for p in original_lock.package}
    assert original_packages["tzdata"].url.endswith(".conda")
    assert original_packages["zlib"].url.endswith(".tar.bz2")
    assert original_packages["ca-certificates"].url.endswith(".conda")

    # 3. Update the lockfile, changing only tzdata
    update_env_file = tmp_path / "environment.update.yml"
    update_env_content = f"""
name: test-env
channels:
  - {channel_url}
dependencies:
  - tzdata=2023a  # Updated version
  - zlib=1.2.13
  - ca-certificates=2023.1.10
"""
    update_env_file.write_text(update_env_content)

    run_lock(
        [update_env_file],
        lockfile_path=lockfile_path,
        platforms=["linux-64"],
        update=["tzdata"],
        conda_exe="mamba",
        mapping_url=DEFAULT_MAPPING_URL,
    )

    # 4. Verify the updated lockfile
    updated_lock = parse_conda_lock_file(lockfile_path)
    updated_packages = {p.name: p for p in updated_lock.package}

    # tzdata should be updated
    assert updated_packages["tzdata"].version == "2023a"
    assert updated_packages["tzdata"].url.endswith(".conda")

    # zlib and ca-certificates should be unchanged
    assert updated_packages["zlib"].url == original_packages["zlib"].url
    assert updated_packages["zlib"].hash.sha256 == original_packages["zlib"].hash.sha256
    assert (
        updated_packages["ca-certificates"].url
        == original_packages["ca-certificates"].url
    )
    assert (
        updated_packages["ca-certificates"].hash.sha256
        == original_packages["ca-certificates"].hash.sha256
    )
