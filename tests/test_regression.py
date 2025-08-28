"""This is a test module to ensure that the various changes we've made over time don't
break the functionality of conda-lock.  This is a regression test suite."""

import io
import logging
import shutil
import textwrap

from pathlib import Path
from textwrap import dedent
from typing import Union

import pytest

from conda_lock.conda_lock import run_lock
from conda_lock.invoke_conda import _stderr_to_log, is_micromamba
from conda_lock.lookup import DEFAULT_MAPPING_URL
from conda_lock.models.lock_spec import VersionedDependency
from conda_lock.src_parser import DEFAULT_PLATFORMS
from conda_lock.src_parser.environment_yaml import parse_environment_file


TEST_DIR = Path(__file__).parent


def clone_test_dir(name: Union[str, list[str]], tmp_path: Path) -> Path:
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
    run_lock(
        [tmp_path / "environment.yml"],
        conda_exe=mamba_exe,
        platforms=[platform],
        mapping_url=DEFAULT_MAPPING_URL,
    )


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
    test_dir: list[str],
    filename: str,
):
    """Simple test that asserts that these engieonments can be locked"""
    spec = clone_test_dir(test_dir, tmp_path).joinpath(filename)
    monkeypatch.chdir(spec.parent)
    run_lock([spec], conda_exe=mamba_exe, mapping_url=DEFAULT_MAPPING_URL)


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
    run_lock(
        [pip_environment_regression_gh155],
        conda_exe=conda_exe,
        mapping_url=DEFAULT_MAPPING_URL,
    )


@pytest.fixture
def pip_environment_regression_gh449(tmp_path: Path):
    return clone_test_dir("test-pypi-resolve-gh449", tmp_path).joinpath(
        "environment.yml"
    )


def test_pip_environment_regression_gh449(pip_environment_regression_gh449: Path):
    res = parse_environment_file(
        pip_environment_regression_gh449,
        DEFAULT_PLATFORMS,
        mapping_url=DEFAULT_MAPPING_URL,
    )
    for plat in DEFAULT_PLATFORMS:
        assert [dep for dep in res.dependencies[plat] if dep.manager == "pip"] == [
            VersionedDependency(
                name="pydantic",
                manager="pip",
                category="main",
                extras=["dotenv", "email"],
                version="==1.10.10",
            )
        ]


@pytest.mark.parametrize(
    ["default_level", "expected_default_level", "override_level"],
    [
        (None, "ERROR", None),  # Test default behavior when env vars are not set
        ("INFO", "INFO", None),  # Test configurable default level
        ("DEBUG", "DEBUG", None),
        ("WARNING", "WARNING", None),
        (None, "DEBUG", "DEBUG"),  # Test override level
        ("INFO", "WARNING", "WARNING"),  # Override should take precedence over default
        ("ERROR", "INFO", "INFO"),
    ],
)
def test_stderr_to_log_gh770(
    caplog, monkeypatch, default_level, expected_default_level, override_level
):
    """Test the configurable log level behavior of _stderr_to_log.

    The function _stderr_to_log processes stderr output from subprocesses with the following rules:
    1. If CONDA_LOCK_SUBPROCESS_STDERR_LOG_LEVEL_OVERRIDE is set, all lines are logged
       at that level, regardless of content
    2. Otherwise:
       a. Lines starting with a known log level prefix are logged at that level:
          - mamba style: "debug    ", "info     ", "warning  ", etc.
          - conda style: "DEBUG conda.core", "INFO conda.fetch", etc.
       b. Indented lines (starting with spaces) inherit the previous line's log level
       c. All other lines are logged at the configured default level, which can be set via
          the CONDA_LOCK_SUBPROCESS_STDERR_DEFAULT_LOG_LEVEL environment variable
       d. If no default level is configured, non-warning lines are logged at ERROR level

    See: https://github.com/conda/conda-lock/issues/770
    """
    # Configure environment
    if default_level is not None:
        monkeypatch.setenv(
            "CONDA_LOCK_SUBPROCESS_STDERR_DEFAULT_LOG_LEVEL", default_level
        )
    else:
        monkeypatch.delenv(
            "CONDA_LOCK_SUBPROCESS_STDERR_DEFAULT_LOG_LEVEL", raising=False
        )

    if override_level is not None:
        monkeypatch.setenv(
            "CONDA_LOCK_SUBPROCESS_STDERR_LOG_LEVEL_OVERRIDE", override_level
        )
        expected_level = (
            override_level  # When override is set, all lines use this level
        )
    else:
        monkeypatch.delenv(
            "CONDA_LOCK_SUBPROCESS_STDERR_LOG_LEVEL_OVERRIDE", raising=False
        )
        expected_level = None  # Use the level from expected_records

    fake_stderr = io.StringIO(
        dedent("""\
        Some regular message at start
        warning  libmamba The following files were already present
          - lib/python3.10/site-packages/package/__init__.py
        debug    detailed information
          with indented continuation
        error    something went wrong
          details of the error
        info     regular progress message
        DEBUG conda.gateways.subprocess:subprocess_call(86): ...
          with subprocess details
        INFO conda.fetch.fetch:fetch(45): Getting package from channel
        WARNING conda.core: Deprecation warning
          with more details
        ERROR conda.exceptions: Failed to execute command
        hi
        """)
    )

    # Capture at DEBUG to ensure we see all log levels
    with caplog.at_level(logging.DEBUG):
        result = _stderr_to_log(fake_stderr)

    # The function should return the original lines, inclusive of trailing newlines
    assert result == [line + "\n" for line in fake_stderr.getvalue().splitlines()]

    # Define the expected records based on whether override is in effect
    if override_level is not None:
        # When override is set, all lines should be logged at that level
        expected_records = [
            (override_level, line) for line in fake_stderr.getvalue().splitlines()
        ]
    else:
        # Normal behavior - each line gets its appropriate level
        expected_records = [
            (expected_default_level, "Some regular message at start"),
            ("WARNING", "warning  libmamba The following files were already present"),
            ("WARNING", "  - lib/python3.10/site-packages/package/__init__.py"),
            ("DEBUG", "debug    detailed information"),
            ("DEBUG", "  with indented continuation"),
            ("ERROR", "error    something went wrong"),
            ("ERROR", "  details of the error"),
            ("INFO", "info     regular progress message"),
            ("DEBUG", "DEBUG conda.gateways.subprocess:subprocess_call(86): ..."),
            ("DEBUG", "  with subprocess details"),
            ("INFO", "INFO conda.fetch.fetch:fetch(45): Getting package from channel"),
            ("WARNING", "WARNING conda.core: Deprecation warning"),
            ("WARNING", "  with more details"),
            ("ERROR", "ERROR conda.exceptions: Failed to execute command"),
            (expected_default_level, "hi"),  # Test short line
        ]

    assert len(caplog.records) == len(expected_records)
    for record, (expected_level, expected_message) in zip(
        caplog.records, expected_records
    ):
        assert record.levelname == expected_level, (
            f"Expected level {expected_level} but got {record.levelname}"
        )
        assert record.message == expected_message, (
            f"Expected message '{expected_message}' but got '{record.message}'"
        )
