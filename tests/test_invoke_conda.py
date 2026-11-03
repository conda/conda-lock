import subprocess
import sys
import time

from pathlib import Path
from typing import IO

import pytest

from conda_lock import invoke_conda
from conda_lock.invoke_conda import _invoke_conda


FAKE_CONDA = Path(__file__).parent / "test-invoke-conda" / "fake_conda.py"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX only")
@pytest.mark.parametrize(
    "exception_type", [RuntimeError, KeyboardInterrupt, pytest.fail.Exception]
)
@pytest.mark.parametrize(
    ("child", "grandchild"),
    [
        ("default", "default"),
        ("ignore", "ignore"),
        ("exit", "ignore"),  # The parent exits before the pipes close.
    ],
)
def test_invoke_conda_stops_child_tree_on_exception(
    tmp_path, monkeypatch, child, grandchild, exception_type
):
    monkeypatch.setenv("FAKE_CONDA_CHILD", child)
    monkeypatch.setenv("FAKE_CONDA_GRANDCHILD", grandchild)
    monkeypatch.setattr(invoke_conda, "_TERMINATE_GRACE_SECONDS", 0.5)
    interruption = exception_type("interrupted")

    def fail_once_the_grandchild_is_running(stderr: IO[str]) -> list[str]:
        assert stderr.readline() == "ready\n"
        raise interruption

    monkeypatch.setattr(
        invoke_conda, "_stderr_to_log", fail_once_the_grandchild_is_running
    )

    start = time.monotonic()
    with pytest.raises(exception_type, match="interrupted") as exc_info:
        _invoke_conda(
            sys.executable,
            prefix=str(tmp_path / "env"),
            name=None,
            command_args=[FAKE_CONDA, "create"],
        )
    assert exc_info.value is interruption
    assert time.monotonic() - start < 5


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "raises_permission_error"),
    [
        (0, "Z\nZ+\n", "", False),
        (1, "", "", False),
        (0, "S\n", "", True),
        (0, "Z\nS\n", "", True),
        (1, "", "ps failed", True),
        (2, "", "", True),
    ],
)
def test_macos_zombie_process_group(
    monkeypatch, returncode, stdout, stderr, raises_permission_error
):
    permission_error = PermissionError("cannot signal group")

    def deny_signal(pgid, sig):
        raise permission_error

    monkeypatch.setattr(invoke_conda.os, "killpg", deny_signal, raising=False)
    monkeypatch.setattr(invoke_conda.sys, "platform", "darwin")
    monkeypatch.setattr(
        invoke_conda.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, stdout, stderr
        ),
    )

    if raises_permission_error:
        with pytest.raises(PermissionError) as exc_info:
            invoke_conda._signal_process_group(123, 0)
        assert exc_info.value is permission_error
    else:
        assert invoke_conda._signal_process_group(123, 0) is False
