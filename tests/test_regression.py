"""This is a test module to ensure that the various changes we've made over time don't
break the functionality of conda-lock.  This is a regression test suite."""

import shutil
import sys
import textwrap

from pathlib import Path
from typing import List, Union

import pytest

from conda_lock.conda_lock import run_lock
from conda_lock.invoke_conda import is_micromamba
from conda_lock.models.lock_spec import VersionedDependency
from conda_lock.src_parser import DEFAULT_PLATFORMS
from conda_lock.src_parser.environment_yaml import parse_environment_file


TEST_DIR = Path(__file__).parent


def clone_test_dir(name: Union[str, List[str]], tmp_path: Path) -> Path:
    if isinstance(name, str):
        name = [name]
    test_dir = TEST_DIR.joinpath(*name)
    assert test_dir.exists()
    assert test_dir.is_dir()
    shutil.copytree(test_dir, tmp_path, dirs_exist_ok=True)
    return tmp_path


@pytest.mark.parametrize("platform", ["linux-64", "osx-64", "osx-arm64"])
def test_pr_436(
    mamba_exe: Path, monkeypatch: "pytest.MonkeyPatch", tmp_path: Path, platform: str
) -> None:
    """Ensure that we can lock this environment which requires more modern osx path selectors"""
    spec = textwrap.dedent(
        """
        channels:
        - conda-forge
        dependencies:
        - python 3.11
        - pip:
            - drjit==0.4.2
        """
    )
    (tmp_path / "environment.yml").write_text(spec)
    monkeypatch.chdir(tmp_path)
    run_lock([tmp_path / "environment.yml"], conda_exe=mamba_exe, platforms=[platform])


@pytest.mark.parametrize(
    ["test_dir", "filename"],
    [
        (["test-pypi-resolve-gh290", "pyproject"], "pyproject.toml"),
        (["test-pypi-resolve-gh290", "tzdata"], "environment.yaml"),
        (["test-pypi-resolve-gh290", "wdl"], "environment.yaml"),
    ],
)
def test_conda_pip_regressions_gh290(
    tmp_path: Path,
    mamba_exe: str,
    monkeypatch: "pytest.MonkeyPatch",
    test_dir: List[str],
    filename: str,
):
    """Simple test that asserts that these engieonments can be locked"""
    spec = clone_test_dir(test_dir, tmp_path).joinpath(filename)
    monkeypatch.chdir(spec.parent)
    run_lock([spec], conda_exe=mamba_exe)


@pytest.fixture
def pip_environment_regression_gh155(tmp_path: Path):
    return clone_test_dir("test-pypi-resolve-gh155", tmp_path).joinpath(
        "environment.yml"
    )


def test_run_lock_regression_gh155(
    monkeypatch: "pytest.MonkeyPatch",
    pip_environment_regression_gh155: Path,
    conda_exe: str,
):
    monkeypatch.chdir(pip_environment_regression_gh155.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([pip_environment_regression_gh155], conda_exe=conda_exe)


@pytest.fixture
def pip_environment_regression_gh449(tmp_path: Path):
    return clone_test_dir("test-pypi-resolve-gh449", tmp_path).joinpath(
        "environment.yml"
    )


def test_pip_environment_regression_gh449(pip_environment_regression_gh449: Path):
    res = parse_environment_file(pip_environment_regression_gh449, DEFAULT_PLATFORMS)
    for plat in DEFAULT_PLATFORMS:
        assert [dep for dep in res.dependencies[plat] if dep.manager == "pip"] == [
            VersionedDependency(
                name="pydantic",
                manager="pip",
                category="main",
                extras=["dotenv", "email"],
                version="=1.10.10",
            )
        ]
