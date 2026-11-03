import sys
import time

from typing import IO

import pytest

from conda_lock import invoke_conda
from conda_lock.invoke_conda import _invoke_conda


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX only")
@pytest.mark.parametrize("child_ignores_sigint", [False, True])
def test_invoke_conda_stops_child_tree_on_exception(
    tmp_path, monkeypatch, child_ignores_sigint
):
    """An exception escaping _invoke_conda must not wait for a hung child.

    The fake conda runs `sleep` as a grandchild that keeps the output pipes open,
    which is what used to block `Popen.__exit__` until the child finished.
    """
    fake_conda = tmp_path / "conda"
    trap = "trap '' INT\n" if child_ignores_sigint else ""
    fake_conda.write_text(f"#!/bin/sh\n{trap}echo ready >&2\nsleep 15\n")
    fake_conda.chmod(0o755)
    monkeypatch.setattr(invoke_conda, "_TERMINATE_GRACE_SECONDS", 0.5, raising=False)

    def fail_once_conda_is_running(stderr: IO[str]) -> list[str]:
        assert stderr.readline() == "ready\n"
        raise RuntimeError("interrupted")

    monkeypatch.setattr(invoke_conda, "_stderr_to_log", fail_once_conda_is_running)

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="interrupted"):
        _invoke_conda(
            fake_conda, prefix=str(tmp_path / "env"), name=None, command_args=["create"]
        )
    assert time.monotonic() - start < 5
