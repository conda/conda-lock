import json
import pathlib
import shutil
import subprocess
import sys

from typing import Any, MutableSequence

import pytest

from conda_lock.conda_lock import (
    PathLike,
    aggregate_lock_specs,
    conda_env_override,
    create_lockfile_from_spec,
    determine_conda_executable,
    main,
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


def test_aggregate_lock_specs():
    gpu_spec = LockSpecification(
        specs=["pytorch"],
        channels=["pytorch", "conda-forge"],
        platform="linux-64",
    )

    base_spec = LockSpecification(
        specs=["python =3.7"],
        channels=["conda-forge"],
        platform="linux-64",
    )

    assert (
        aggregate_lock_specs([gpu_spec, base_spec]).env_hash()
        == LockSpecification(
            specs=["pytorch", "python =3.7"],
            channels=["pytorch", "conda-forge"],
            platform="linux-64",
        ).env_hash()
    )

    assert (
        aggregate_lock_specs([base_spec, gpu_spec]).env_hash()
        == LockSpecification(
            specs=["pytorch", "python =3.7"],
            channels=["conda-forge"],
            platform="linux-64",
        ).env_hash()
    )


def _create_conda_env(conda: PathLike, name: str) -> bool:
    args: MutableSequence[PathLike] = [
        str(conda),
        "create",
        "--name",
        name,
    ]

    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    def print_proc(proc):
        print(f"    Command: {proc.args}")
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}")
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}")

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        try:
            err_json = json.loads(proc.stdout)
            message = err_json["message"]
        except json.JSONDecodeError as e:
            print(f"Failed to parse json, {e}")
            message = ""

        print("Could not perform conda create")
        if message:
            print(message)
        print_proc(proc)

        return False
    return True


def _destroy_conda_env(conda: PathLike, name: str) -> bool:
    args: MutableSequence[PathLike] = [str(conda), "remove", "--name", name, "--all"]

    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    def print_proc(proc):
        print(f"    Command: {proc.args}")
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}")
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}")

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        try:
            err_json = json.loads(proc.stdout)
            message = err_json["message"]
        except json.JSONDecodeError as e:
            print(f"Failed to parse json, {e}")
            message = ""

        print("Could not perform conda remove")
        if message:
            print(message)
        print_proc(proc)

        return False
    return True


@pytest.fixture
def conda_exe():
    return determine_conda_executable("conda", no_mamba=True)


@pytest.fixture
def conda_env(conda_exe):
    env_name = "test"
    _create_conda_env(conda_exe, name=env_name)
    yield env_name
    _destroy_conda_env(conda_exe, name=env_name)


def _check_package_installed(conda: PathLike, package: str, platform: str, name: str):
    args: MutableSequence[PathLike] = [str(conda), "list", "--name", name, package]

    proc = subprocess.run(
        args,
        env=conda_env_override(platform),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    def print_proc(proc):
        print(f"    Command: {proc.args}")
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}")
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}")

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        try:
            err_json = json.loads(proc.stdout)
            message = err_json["message"]
        except json.JSONDecodeError as e:
            print(f"Failed to parse json, {e}")
            message = ""

        print(f"Could not lock the environment for platform {platform}")
        if message:
            print(message)
        print_proc(proc)

        sys.exit(1)

    return package in proc.stdout


def test_install(conda_env, tmp_path, conda_exe):
    environment_file = tmp_path / "environment.yml"
    package = "click"
    platform = "linux-64"
    environment_file.write_text(
        f"""
    channels:
      - conda-forge
    dependencies:
      - python=3.8.5
      - {package}"""
    )

    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(main, ["lock", "-p", platform, "-f", environment_file])
    assert result.exit_code == 0

    result = runner.invoke(main, ["install", "--name", conda_env, "conda-osx-64.lock"])
    assert result.exit_code == 0

    _check_package_installed(
        conda=conda_exe, package=package, platform=platform, name=conda_env
    )
