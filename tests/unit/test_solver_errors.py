import json
import subprocess

import pytest

from conda_lock import conda_solver


@pytest.mark.parametrize(
    "stdout,stderr,expected",
    [
        ("pin conflict: root ==1", "", "pin conflict: root ==1"),
        ('banner\n{"message": "unsatisfiable"}\n', "", "unsatisfiable"),
        (
            json.dumps(
                {
                    "log_history": [
                        {"level": "info", "message": "loading"},
                        {"level": "error", "message": "root conflicts with pin"},
                    ]
                }
            ),
            "",
            "root conflicts with pin",
        ),
        ("", "cannot connect", "cannot connect"),
        ("", "", "solver exited with status 1"),
    ],
)
def test_update_failure_preserves_solver_diagnostics(
    monkeypatch, stdout, stderr, expected
):
    from tests.unit.test_metadata_fallback import make_locked

    locked = make_locked({"root": {}})
    records = {name: dep.to_fetch_action() for name, dep in locked.items()}
    monkeypatch.setattr(
        conda_solver, "verified_channel_records", lambda *args, **kwargs: records
    )
    monkeypatch.setattr(
        conda_solver, "_get_installed_conda_packages", lambda *args: records
    )
    monkeypatch.setattr(
        conda_solver.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout, stderr
        ),
    )
    with pytest.raises(RuntimeError) as error:
        conda_solver.update_specs_for_arch(
            conda="conda",
            specs=["root"],
            locked=locked,
            update=["root"],
            platform="linux-64",
            channels=[],
        )
    assert expected in str(error.value)
    assert isinstance(error.value.__cause__, subprocess.CalledProcessError)
