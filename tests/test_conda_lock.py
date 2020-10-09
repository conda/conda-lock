import pathlib
import shutil

from typing import Any

import pytest

from conda_lock.conda_lock import (
    create_lockfile_from_spec,
    determine_conda_executable,
    parse_meta_yaml_file,
    run_lock,
)
from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.pyproject_toml import (
    parse_flit_pyproject_toml,
    parse_poetry_pyproject_toml,
    poetry_version_to_conda_version,
    to_match_spec,
)


@pytest.fixture
def gdal_environment():
    return pathlib.Path(__file__).parent.joinpath("gdal").joinpath("environment.yml")


@pytest.fixture
def zlib_environment():
    return pathlib.Path(__file__).parent.joinpath("zlib").joinpath("environment.yml")


@pytest.fixture
def meta_yaml_environment():
    return pathlib.Path(__file__).parent.joinpath("test-recipe").joinpath("meta.yaml")


@pytest.fixture
def poetry_pyproject_toml():
    return (
        pathlib.Path(__file__).parent.joinpath("test-poetry").joinpath("pyproject.toml")
    )


@pytest.fixture
def flit_pyproject_toml():
    return (
        pathlib.Path(__file__).parent.joinpath("test-flit").joinpath("pyproject.toml")
    )


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(True, id="--dev-dependencies"),
        pytest.param(False, id="--no-dev-dependencies"),
    ],
)
def include_dev_dependencies(request: Any) -> bool:
    return request.param


def test_parse_environment_file(gdal_environment):
    res = parse_environment_file(gdal_environment, "linux-64")
    assert all(x in res.specs for x in ["python >=3.7,<3.8", "gdal"])
    assert all(x in res.channels for x in ["conda-forge", "defaults"])


def test_parse_meta_yaml_file(meta_yaml_environment, include_dev_dependencies):
    res = parse_meta_yaml_file(
        meta_yaml_environment,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
    )
    assert all(x in res.specs for x in ["python", "numpy"])
    # Ensure that this dep specified by a python selector is ignored
    assert "enum34" not in res.specs
    # Ensure that this platform specific dep is included
    assert "zlib" in res.specs
    assert ("pytest" in res.specs) == include_dev_dependencies


def test_parse_poetry(poetry_pyproject_toml, include_dev_dependencies):
    res = parse_poetry_pyproject_toml(
        poetry_pyproject_toml,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
    )

    assert "requests[version>=2.13.0,<3.0.0]" in res.specs
    assert "toml[version>=0.10]" in res.specs
    assert ("pytest[version>=5.1.0,<5.2.0]" in res.specs) == include_dev_dependencies
    assert res.channels == ["defaults"]


def test_parse_flit(flit_pyproject_toml, include_dev_dependencies):
    res = parse_flit_pyproject_toml(
        flit_pyproject_toml,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
    )

    assert "requests[version>=2.13.0]" in res.specs
    assert "toml[version>=0.10]" in res.specs
    # test deps
    assert ("pytest[version>=5.1.0]" in res.specs) == include_dev_dependencies
    assert res.channels == ["defaults"]


def test_run_lock_conda(monkeypatch, zlib_environment):
    monkeypatch.chdir(zlib_environment.parent)
    run_lock([zlib_environment], conda_exe="conda")


def test_run_lock_mamba(monkeypatch, zlib_environment):
    if not shutil.which("mamba"):
        raise pytest.skip("mamba is not installed")
    monkeypatch.chdir(zlib_environment.parent)
    run_lock([zlib_environment], conda_exe="mamba")


@pytest.mark.parametrize(
    "package,version,url_pattern",
    [
        ("python", ">=3.6,<3.7", "/python-3.6"),
        ("python", "~3.6", "/python-3.6"),
        ("python", "^2.7", "/python-2.7"),
    ],
)
def test_poetry_version_parsing_constraints(package, version, url_pattern):
    _conda_exe = determine_conda_executable("conda", no_mamba=True)
    spec = LockSpecification(
        specs=[to_match_spec(package, poetry_version_to_conda_version(version))],
        channels=["conda-forge"],
        platform="linux-64",
    )
    lockfile_contents = create_lockfile_from_spec(
        conda=_conda_exe, channels=spec.channels, spec=spec
    )

    for line in lockfile_contents:
        if url_pattern in line:
            break
    else:
        raise ValueError(f"could not find {package} {version}")
