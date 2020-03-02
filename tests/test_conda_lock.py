import pathlib
import shutil

from conda_lock.conda_lock import ensure_conda, parse_environment_file


def test_ensure_conda_nopath():
    assert pathlib.Path(ensure_conda()).is_file()


def test_ensure_conda_path():
    conda_executable = shutil.which("conda") or shutil.which("conda.exe")
    assert conda_executable == ensure_conda(conda_executable)


def test_parse_environment_file():
    fname = pathlib.Path(__file__).parent.joinpath("environment.yml")
    res = parse_environment_file(fname)
    assert all(x in res["specs"] for x in ["python >=3.7,<3.8", "gdal"])
    assert all(x in res["channels"] for x in ["conda-forge", "defaults"])
