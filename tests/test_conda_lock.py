import pathlib
import shutil

from conda_lock.conda_lock import (
    ensure_conda,
    install_conda_exe,
    parse_environment_file,
)


def test_ensure_conda_nopath():
    assert pathlib.Path(ensure_conda()).is_file()


def test_ensure_conda_path():
    conda_executable = shutil.which("conda") or shutil.which("conda.exe")
    assert conda_executable == ensure_conda(conda_executable)


def test_install_conda_exe():
    target_filename = install_conda_exe()
    target_filename == ensure_conda(target_filename)


def test_parse_environment_file():
    fname = pathlib.Path(__file__).parent.joinpath("environment.yml")
    res = parse_environment_file(fname)
    assert all(x in res["specs"] for x in ["python >=3.7,<3.8", "gdal"])
    assert all(x in res["channels"] for x in ["conda-forge", "defaults"])
