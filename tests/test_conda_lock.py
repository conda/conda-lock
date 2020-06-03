import pathlib
import shutil

import pytest

from conda_lock.conda_lock import (
    ensure_conda,
    install_conda_exe,
    parse_environment_file,
    run_lock,
)


@pytest.fixture
def gdal_environment():
    return pathlib.Path(__file__).parent.joinpath("gdal").joinpath("environment.yml")


@pytest.fixture
def zlib_environment():
    return pathlib.Path(__file__).parent.joinpath("zlib").joinpath("environment.yml")


def test_ensure_conda_nopath():
    assert pathlib.Path(ensure_conda()).is_file()


def test_ensure_conda_path():
    conda_executable = shutil.which("conda") or shutil.which("conda.exe")
    assert pathlib.Path(conda_executable) == ensure_conda(conda_executable)


def test_install_conda_exe():
    target_filename = install_conda_exe()
    assert target_filename == ensure_conda(target_filename)


def test_parse_environment_file(gdal_environment):
    res = parse_environment_file(gdal_environment)
    assert all(x in res["specs"] for x in ["python >=3.7,<3.8", "gdal"])
    assert all(x in res["channels"] for x in ["conda-forge", "defaults"])


def test_run_lock_conda(monkeypatch, zlib_environment):
    monkeypatch.chdir(zlib_environment.parent)
    run_lock(zlib_environment, conda_exe="conda")


def test_run_lock_mamba(monkeypatch, zlib_environment):
    if not shutil.which("mamba"):
        raise pytest.skip("mamba is not installed")
    monkeypatch.chdir(zlib_environment.parent)
    run_lock(zlib_environment, conda_exe="mamba")
