import os
import subprocess

from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.utils._compat import decode

from .git import Git


def get_vcs(directory):  # type: (Path) -> Git
    working_dir = Path.cwd()
    os.chdir(str(directory.resolve()))

    try:
        from .git import executable

        git_dir = decode(
            subprocess.check_output(
                [executable(), "rev-parse", "--show-toplevel"], stderr=subprocess.STDOUT
            )
        ).strip()

        vcs = Git(Path(git_dir))

    except (subprocess.CalledProcessError, OSError, RuntimeError):
        vcs = None
    finally:
        os.chdir(str(working_dir))

    return vcs
