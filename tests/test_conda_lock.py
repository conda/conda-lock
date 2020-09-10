import pathlib
import shutil

from typing import Any

import pytest

from conda_lock.conda_lock import (
    ensure_conda,
    install_conda_exe,
    parse_meta_yaml_file,
    run_lock,
)
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.pyproject_toml import (
    parse_flit_pyproject_toml,
    parse_poetry_pyproject_toml,
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


def test_ensure_conda_nopath():
    assert pathlib.Path(ensure_conda()).is_file()


def test_ensure_conda_path():
    conda_executable = shutil.which("conda") or shutil.which("conda.exe")
    assert pathlib.Path(conda_executable) == ensure_conda(conda_executable)


def test_install_conda_exe():
    target_filename = install_conda_exe()
    assert pathlib.Path(target_filename) == ensure_conda(target_filename)


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
    run_lock(zlib_environment, conda_exe="conda")


def test_run_lock_mamba(monkeypatch, zlib_environment):
    if not shutil.which("mamba"):
        raise pytest.skip("mamba is not installed")
    monkeypatch.chdir(zlib_environment.parent)
    run_lock(zlib_environment, conda_exe="mamba")
