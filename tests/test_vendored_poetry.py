import sys
import tarfile
import zipfile

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest

from conda_lock._vendor.poetry.installation.wheel_installer import WheelInstaller
from conda_lock._vendor.poetry.utils.env import Env
from conda_lock._vendor.poetry.utils.helpers import extractall
from conda_lock.interfaces.vendored_poetry import Factory, Link


def test_schema_resources_use_vendored_package() -> None:
    data = {"tool": {"poetry": {"name": "fixture-vcs", "version": "1.2.3"}}}
    assert Factory.validate(data)["errors"] == []
    data["tool"]["poetry"]["unexpected-property"] = "invalid"
    assert Factory.validate(data)["errors"] == [
        "Additional properties are not allowed ('unexpected-property' was unexpected)"
    ]


@pytest.mark.parametrize("filename", ["../outside.whl", r"..\outside.whl"])
def test_reject_index_filename_traversal(filename: str) -> None:
    link = Link("https://example.invalid/package.whl", filename=filename)
    with pytest.raises(ValueError, match="Invalid filename"):
        _ = link.filename


@pytest.mark.parametrize("use_fallback", [False, True])
def test_sdist_extraction_stays_in_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_fallback: bool
) -> None:
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("../outside.txt")
        member.size = len(b"outside")
        tar.addfile(member, BytesIO(b"outside"))

    if use_fallback:
        # Exercise Poetry's protection on Python patch versions without data_filter.
        monkeypatch.delattr(tarfile, "data_filter", raising=False)

    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises((ValueError, tarfile.TarError)):
        extractall(archive, destination, zip=False)
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize(
    "member",
    ["../../outside.txt", "../site-packages-sibling/outside.txt"],
)
def test_wheel_installation_rejects_traversal_before_writing(
    tmp_path: Path, member: str
) -> None:
    destination = tmp_path / "environment" / "site-packages"
    destination.mkdir(parents=True)
    outside = (destination / member).resolve()
    wheel = tmp_path / "fixture-1.0-py3-none-any.whl"
    files = {
        "fixture/__init__.py": "",
        member: "must not be written",
        "fixture-1.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "fixture-1.0.dist-info/METADATA": (
            "Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n"
        ),
    }
    files["fixture-1.0.dist-info/RECORD"] = (
        "".join(f"{name},,\n" for name in files) + "fixture-1.0.dist-info/RECORD,,\n"
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)

    scheme = {
        key: str(destination)
        for key in ["purelib", "platlib", "scripts", "include", "headers", "data"]
    }
    env = Mock(spec=Env, scheme_dict=scheme, python=Path(sys.executable))
    with pytest.raises(ValueError, match="outside of the target directory"):
        WheelInstaller(env).install(wheel)
    # An exception alone is insufficient: Poetry 2.0.1 wrote before failing.
    assert not outside.exists()
