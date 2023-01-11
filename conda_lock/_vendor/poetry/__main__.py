from __future__ import annotations

import sys


if __name__ == "__main__":
    from conda_lock._vendor.poetry.console.application import main

    sys.exit(main())
