from pathlib import Path

from conda_lock.conda_lock import make_lock_spec
from conda_lock.content_hash import (
    backwards_compatible_content_hashes,
    compute_content_hashes,
)
from conda_lock.lookup import DEFAULT_MAPPING_URL
from conda_lock.virtual_package import default_virtual_package_repodata


HASHES_V2 = {
    "linux-64": "4e3086c79ebb7044f221959819fbca22e3ad4144b2723482e48f2cffef1cb948",
    "linux-aarch64": "07b90c11b3b0bb858767afd42d753952d0f1c6df852771b0d5d2d3f495628cfa",
    "linux-ppc64le": "39107ca32794f20f9b443d2d44862b5d06184164df3f851f56901fd0d69483e9",
    "osx-64": "d8bfcbde7a20bc50b27ca25139f0d18ee48d21905c7482722c120793713144b1",
    "osx-arm64": "e5b0208328748fdbbf872160bf8e5aff48d3fd5f38fde26e12dcd72a32d5a0d7",
    "win-64": "a67c2def7fa06f94d92df2f17e7c7c940efbb0998a92788a2c1c4feddd605579",
}
"""Hashes corresponding to the v2 and v3.0.3 lock files"""

REGRESSED_HASHES_V3_0_2 = {
    "linux-64": "5d7f1201e3637f6c815681456b2c1a66ed4eb02a4fb01ea0b55e4fadac9b6486",
    "linux-aarch64": "42debda8406741c455ca65f8c11677cd74643e5ca138596b192302a65c415905",
    "linux-ppc64le": "aa74f942434f58455399641ed6c0cd381ec6f15ad1f48562b20bdfde1d33e3fc",
    "osx-64": "f7aa7e865bc376a5c93b94469853849ecdf5be5a29e007d38df87ef0070d0225",
    "osx-arm64": "2ca52189d3c9857abfd0756ca1bb1c190d8e28a3677ff584bb4756859349211b",
    "win-64": "62a65caecc25ce8d3a174ddeb2fb317df32cbc3e1ed3b91f8c6fb82c52f513e0",
}
"""Hashes corresponding to the v3.0.0, v3.0.1, v3.0.2 lock files"""


def test_v2_hash_stability():
    expected = HASHES_V2
    test_path = Path(__file__).parent
    src_files = [
        test_path / "environment.yml",
        test_path / "test-dependencies.yml",
        test_path / "dev-dependencies.yml",
        test_path / "pyproject.toml",
    ]
    lock_spec = make_lock_spec(src_files=src_files, mapping_url=DEFAULT_MAPPING_URL)
    virtual_package_repo = default_virtual_package_repodata()
    computed = compute_content_hashes(lock_spec, virtual_package_repo)
    assert computed == expected


def test_v3_0_2_hash_compatibility():
    expected = REGRESSED_HASHES_V3_0_2
    test_path = Path(__file__).parent
    src_files = [
        test_path / "environment.yml",
        test_path / "test-dependencies.yml",
        test_path / "dev-dependencies.yml",
        test_path / "pyproject.toml",
    ]
    lock_spec = make_lock_spec(src_files=src_files, mapping_url=DEFAULT_MAPPING_URL)
    virtual_package_repo = default_virtual_package_repodata()
    for platform, expected_hash in expected.items():
        assert expected_hash in backwards_compatible_content_hashes(
            lock_spec, virtual_package_repo, platform
        )
