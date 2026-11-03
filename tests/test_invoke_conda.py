import sys
import time

from typing import IO

import pytest

from conda_lock import invoke_conda
from conda_lock.invoke_conda import _invoke_conda


# The grandchild holds the output pipes open. Wait for its SIGINT handler
# to be installed before interrupting the process group.
FAKE_CONDA = """\
import os
import signal
import subprocess
import sys
import time

HANDLERS = {
    "default": signal.SIG_DFL,
    "ignore": signal.SIG_IGN,
    "exit": lambda *_: sys.exit(0),
}
if sys.argv[1:] == ["grandchild"]:
    signal.signal(signal.SIGINT, HANDLERS[os.environ["FAKE_CONDA_GRANDCHILD"]])
    print("ready", file=sys.stderr, flush=True)
else:
    signal.signal(signal.SIGINT, HANDLERS[os.environ["FAKE_CONDA_CHILD"]])
    subprocess.Popen([sys.executable, __file__, "grandchild"])
time.sleep(15)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX only")
@pytest.mark.parametrize(
    ("child", "grandchild"),
    [
        ("default", "default"),
        ("ignore", "ignore"),
        ("exit", "ignore"),  # The parent exits before the pipes close.
    ],
)
def test_invoke_conda_stops_child_tree_on_exception(
    tmp_path, monkeypatch, child, grandchild
):
    script = tmp_path / "fake_conda.py"
    script.write_text(FAKE_CONDA)
    fake_conda = tmp_path / "conda"
    fake_conda.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    fake_conda.chmod(0o755)
    monkeypatch.setenv("FAKE_CONDA_CHILD", child)
    monkeypatch.setenv("FAKE_CONDA_GRANDCHILD", grandchild)
    monkeypatch.setattr(invoke_conda, "_TERMINATE_GRACE_SECONDS", 0.5)

    def fail_once_the_grandchild_is_running(stderr: IO[str]) -> list[str]:
        assert stderr.readline() == "ready\n"
        raise RuntimeError("interrupted")

    monkeypatch.setattr(
        invoke_conda, "_stderr_to_log", fail_once_the_grandchild_is_running
    )

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="interrupted"):
        _invoke_conda(
            fake_conda, prefix=str(tmp_path / "env"), name=None, command_args=["create"]
        )
    assert time.monotonic() - start < 5
