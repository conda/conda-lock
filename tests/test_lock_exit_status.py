from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from click.testing import CliRunner

from conda_lock import conda_lock
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency


CHECK_FLAGS = ["--check-input-hash", "--fail-on-no-change"]


@pytest.fixture
def solver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Keep real parsing, hashing, and file writes; replace external solver calls."""
    monkeypatch.chdir(tmp_path)
    Path("environment.yml").write_text(
        "channels: [conda-forge]\nplatforms: [linux-64, osx-64]\ndependencies: [zlib]\n"
    )
    monkeypatch.setattr(
        conda_lock, "determine_conda_executable", Mock(return_value="conda")
    )
    monkeypatch.setattr(
        conda_lock.subprocess, "check_output", Mock(return_value="conda 26.1.0")
    )

    def solve(*args: Any, platform: str, **kwargs: Any) -> dict[str, LockedDependency]:
        return {
            "zlib": LockedDependency(
                name="zlib",
                version="1.3.1",
                manager="conda",
                platform=platform,
                url=f"https://conda.anaconda.org/conda-forge/{platform}/zlib-1.3.1-0.conda",
                hash=HashModel(md5="0" * 32),
                categories={"main"},
            )
        }

    mock_solver = Mock(side_effect=solve)
    monkeypatch.setattr(conda_lock, "solve_conda", mock_solver)
    return mock_solver


@pytest.mark.parametrize("command", [[], ["lock"]])
@pytest.mark.parametrize(
    "kinds",
    [[], ["-k", "lock"], ["-k", "lock", "-k", "env"]],
)
def test_no_change_exit_status_is_opt_in(
    solver: Mock, command: list[str], kinds: list[str]
) -> None:
    runner = CliRunner()
    args = [*command, *kinds, *CHECK_FLAGS]
    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 0, result.output
    assert solver.call_count == 2
    original_mtimes = {path: path.stat().st_mtime_ns for path in Path().glob("conda-*")}

    solver.reset_mock()
    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 4, result.output
    solver.assert_not_called()
    assert all(
        path.stat().st_mtime_ns == mtime for path, mtime in original_mtimes.items()
    )

    result = runner.invoke(conda_lock.main, [*command, *kinds, "--check-input-hash"])
    assert result.exit_code == 0, result.output
    solver.assert_not_called()


@pytest.mark.parametrize("kind", ["explicit", "env"])
def test_render_only_outputs_can_be_skipped(solver: Mock, kind: str) -> None:
    runner = CliRunner()
    args = ["-k", kind, *CHECK_FLAGS]
    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 0, result.output
    assert not Path("conda-lock.yml").exists()

    solver.reset_mock()
    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 4, result.output
    # Without a unified lockfile, solving still runs; only the outputs are skipped.
    assert solver.call_count == 2


@pytest.mark.parametrize("extension", ["", ".yml"])
def test_missing_output_is_rendered_despite_other_matching_hashes(
    solver: Mock, extension: str
) -> None:
    runner = CliRunner()
    args = [
        "-k",
        "lock",
        "-k",
        "explicit",
        "-k",
        "env",
        "--filename-template",
        "custom-{platform}",
        *CHECK_FLAGS,
    ]
    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 0, result.output
    missing = Path(f"custom-osx-64{extension}")
    expected_content = missing.read_text()
    missing.unlink()
    solver.reset_mock()

    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 0, result.output
    assert missing.read_text() == expected_content
    solver.assert_not_called()

    result = runner.invoke(conda_lock.main, args)
    assert result.exit_code == 4, result.output


def test_one_changed_platform_counts_as_regeneration(solver: Mock) -> None:
    runner = CliRunner()
    result = runner.invoke(conda_lock.main, CHECK_FLAGS)
    assert result.exit_code == 0, result.output
    Path("environment.yml").write_text(
        "channels: [conda-forge]\n"
        "platforms: [linux-64, osx-64]\n"
        "dependencies:\n"
        "  - zlib\n"
        "  - zlib >=1.3  # [osx]\n"
    )
    solver.reset_mock()

    result = runner.invoke(conda_lock.main, CHECK_FLAGS)
    assert result.exit_code == 0, result.output
    solver.assert_called_once()
    assert solver.call_args.kwargs["platform"] == "osx-64"

    result = runner.invoke(conda_lock.main, CHECK_FLAGS)
    assert result.exit_code == 4, result.output


def test_update_counts_as_a_write_even_with_identical_contents(solver: Mock) -> None:
    runner = CliRunner()
    result = runner.invoke(conda_lock.main, CHECK_FLAGS)
    assert result.exit_code == 0, result.output
    contents = Path("conda-lock.yml").read_bytes()
    solver.reset_mock()

    result = runner.invoke(conda_lock.main, [*CHECK_FLAGS, "--update", "zlib"])
    assert result.exit_code == 0, result.output
    assert solver.call_count == 2
    assert Path("conda-lock.yml").read_bytes() == contents


@pytest.mark.parametrize(
    "args, message",
    [
        (["--fail-on-no-change"], "requires --check-input-hash"),
        ([*CHECK_FLAGS, "--strip-auth"], "incompatible with --strip-auth"),
    ],
)
def test_invalid_flag_combinations_fail_before_solving(
    solver: Mock, args: list[str], message: str
) -> None:
    result = CliRunner().invoke(conda_lock.main, args)
    assert result.exit_code == 2, result.output
    assert message in result.output
    solver.assert_not_called()


@pytest.mark.parametrize("failure", ["solve_conda", "render_lockfile_for_platform"])
def test_errors_are_not_reported_as_no_change(
    solver: Mock, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(conda_lock, failure, Mock(side_effect=RuntimeError("failed")))
    result = CliRunner().invoke(conda_lock.main, ["-k", "explicit", *CHECK_FLAGS])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, RuntimeError)
