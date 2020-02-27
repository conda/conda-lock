import pathlib

from conda_lock.conda_lock import parse_environment_file


def test_parse_environment_file():
    fname = pathlib.Path(__file__).parent.joinpath("environment.yml")
    res = parse_environment_file(fname)
    assert all(x in res["specs"] for x in ["python >=3.7,<3.8", "gdal"])
    assert all(x in res["channels"] for x in ["conda-forge", "defaults"])
