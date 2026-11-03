import json
import subprocess
import sys

from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install_refreshed_durations.py"


def install(root: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--refreshed",
            str(root / "refreshed"),
            "--committed",
            str(root / "tests/durations"),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A source tree with tests, a module with doctests, and both durations dirs."""
    (tmp_path / "tests/durations").mkdir(parents=True)
    (tmp_path / "refreshed").mkdir()
    (tmp_path / "tests/test_a.py").write_text(
        "def test_kept():\n"
        "    pass\n"
        "\n"
        "\n"
        "def test_new():\n"
        "    pass\n"
        "\n"
        "\n"
        "class TestGroup:\n"
        "    def test_method(self):\n"
        "        pass\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/mod.py").write_text(
        '"""Doctests."""\n\n\ndef helper():\n    pass\n'
    )
    return tmp_path


def test_keeps_live_tests_and_drops_removed_ones(root: Path):
    committed = root / "tests/durations/linux-py3.json"
    committed.write_text(
        json.dumps(
            {
                # Absent from the refresh, but the tests still exist: kept.
                "tests/test_a.py::test_kept[x]": 1.0,
                "pkg/mod.py::pkg.mod": 2.0,
                "pkg/mod.py::pkg.mod.helper": 3.0,
                # Refreshed: replaced.
                "tests/test_a.py::TestGroup::test_method": 4.0,
                # Absent from the refresh and gone from the source: dropped.
                "tests/test_a.py::test_removed": 5.0,
                "tests/test_gone.py::test_x": 6.0,
            }
        )
    )
    (root / "refreshed/linux-py3.json").write_text(
        json.dumps(
            {
                "tests/test_a.py::TestGroup::test_method": 40.0,
                "tests/test_a.py::test_new": 7.0,
                # A stale artifact may still list a removed test: dropped.
                "tests/test_a.py::test_removed_but_in_old_run": 8.0,
            }
        )
    )
    # A new matrix entry without a committed file is installed too.
    (root / "refreshed/mac-py3.json").write_text(
        json.dumps({"tests/test_a.py::test_new": 9.0})
    )

    result = install(root)

    assert result.returncode == 0, result.stderr
    assert json.loads(committed.read_text()) == {
        "tests/test_a.py::test_kept[x]": 1.0,
        "pkg/mod.py::pkg.mod": 2.0,
        "pkg/mod.py::pkg.mod.helper": 3.0,
        "tests/test_a.py::TestGroup::test_method": 40.0,
        "tests/test_a.py::test_new": 7.0,
    }
    assert committed.read_text().endswith("}\n")
    assert json.loads((root / "tests/durations/mac-py3.json").read_text()) == {
        "tests/test_a.py::test_new": 9.0
    }
    assert "linux-py3.json: 5 entries, 3 kept" in result.stdout


def test_refuses_a_refresh_that_lost_a_file(root: Path):
    committed = root / "tests/durations/linux-py3.json"
    original = json.dumps({"tests/test_a.py::test_kept": 1.0})
    committed.write_text(original)
    (root / "tests/durations/mac-py3.json").write_text(original)
    (root / "refreshed/mac-py3.json").write_text(
        json.dumps({"tests/test_a.py::test_kept": 2.0})
    )

    result = install(root)

    assert result.returncode == 1
    assert "linux-py3.json" in result.stderr
    assert committed.read_text() == original
    assert (root / "tests/durations/mac-py3.json").read_text() == original
