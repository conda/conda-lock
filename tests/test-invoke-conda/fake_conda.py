import os
import signal
import subprocess
import sys
import time


def main() -> None:
    handlers = {
        "default": signal.SIG_DFL,
        "ignore": signal.SIG_IGN,
        "exit": lambda *_: sys.exit(0),
    }
    if sys.argv[1:] == ["grandchild"]:
        signal.signal(signal.SIGINT, handlers[os.environ["FAKE_CONDA_GRANDCHILD"]])
        # Signal readiness only after the handler is installed.
        print("ready", file=sys.stderr, flush=True)
    else:
        signal.signal(signal.SIGINT, handlers[os.environ["FAKE_CONDA_CHILD"]])
        subprocess.Popen([sys.executable, __file__, "grandchild"])
    time.sleep(15)


if __name__ == "__main__":
    main()
