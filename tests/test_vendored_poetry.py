import tarfile

from io import BytesIO
from pathlib import Path

import pytest

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
