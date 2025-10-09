"""
Utility for managing temporary directories with optional preservation.

This module provides two complementary approaches for temporary directory management in conda-lock:

1. **Context-managed directories** (`temporary_directory`): For scoped, short-lived temporary
   directories that should be cleaned up when exiting the context. These behave like
   `tempfile.TemporaryDirectory()` but with optional preservation for debugging.

2. **Process-lifetime directories** (`mkdtemp_with_cleanup`): For temporary directories that need
   to persist for the entire process lifetime but should be cleaned up at exit. These are
   essential for directories that are created once and used across multiple operations.

Design Rationale:
- Conda-lock needs temporary directories that survive beyond individual function scopes but
  should still be cleaned up when the process exits (e.g., fake conda environments, package
  directories, virtual package repositories).

- Python's `tempfile.TemporaryDirectory` with context managers is perfect for scoped usage,
  but doesn't work for process-lifetime directories that need to be passed between functions.

- The `mkdtemp_with_cleanup` function provides process-lifetime directories by using
  `tempfile.mkdtemp()` (which never auto-cleans) and registering cleanup via `atexit`.

- The `delete_temp_paths` flag controls cleanup; set to False to preserve for debugging.
  inspecting intermediate files and directories created during the locking process.

Python Version Notes:
- `tempfile.TemporaryDirectory` supports a `delete=False` parameter in Python 3.12+,
  but this module maintains compatibility with earlier versions by using `mkdtemp()` +
  manual cleanup registration for process-lifetime directories.
"""

import atexit
import os
import pathlib
import shutil
import sys
import tempfile

from collections.abc import Iterator
from contextlib import contextmanager


# Module-level flag controlling deletion of temporary directories
# True => delete on context exit or process exit; False => preserve
delete_temp_paths: bool = True

# List tracking created temp directories when preserved
_preserved_paths: list[str] = []


def _track(path: str) -> None:
    """Track a preserved temporary directory for logging."""
    if len(_preserved_paths) == 0:
        # Register exit handler on first preserved directory
        atexit.register(_log_preserved_paths)

    _preserved_paths.append(path)


@contextmanager
def temporary_directory(
    prefix: str = "conda-lock-", dir: str | None = None
) -> Iterator[str]:
    """
    Create a temporary directory honoring deletion behavior.

    If delete_temp_paths is True (default), the directory is cleaned up on context exit.
    If delete_temp_paths is False, the directory is preserved (and tracked).

    Parameters
    ----------
    prefix : str
        Prefix for the temporary directory name.
    dir : str | None
        Parent directory for the temporary directory.

    Yields
    ------
    str
        Path to the temporary directory.
    """
    if delete_temp_paths:
        # Use standard temporary directory with cleanup
        with tempfile.TemporaryDirectory(prefix=prefix, dir=dir) as tmp_dir:
            yield tmp_dir
    else:
        # Create a directory that won't be cleaned up
        tmp_dir = tempfile.mkdtemp(prefix=prefix, dir=dir)
        _track(tmp_dir)
        yield tmp_dir


def mkdtemp_with_cleanup(prefix: str = "conda-lock-", dir: str | None = None) -> str:
    """
    Create a temporary directory with optional cleanup at process exit.

    If delete_temp_paths is True, cleanup is registered via atexit.
    Otherwise, the directory is preserved and tracked for logging.

    Parameters
    ----------
    prefix : str
        Prefix for the temporary directory name.
    dir : str | None
        Parent directory for the temporary directory.

    Returns
    -------
    str
        Path to the temporary directory.
    """
    path = tempfile.mkdtemp(prefix=prefix, dir=dir)

    if delete_temp_paths:
        # Register cleanup at exit
        atexit.register(lambda: shutil.rmtree(path, ignore_errors=True))
    else:
        # Don't register cleanup; track for logging
        _track(path)

    return path


def _log_preserved_paths() -> None:
    """Log all preserved temporary files and directories."""
    if _preserved_paths:
        print("=" * 60, file=sys.stderr)
        print("Preserved temporary paths:", file=sys.stderr)
        for path in _preserved_paths:
            p = pathlib.Path(path)
            if p.exists():
                if p.is_dir():
                    print(f"  - {p}{os.sep}", file=sys.stderr)
                else:
                    print(f"  - {p}", file=sys.stderr)
            else:
                print(f"  - WARNING: missing path: {p}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)


@contextmanager
def temporary_file_with_contents(content: str) -> Iterator[pathlib.Path]:
    """Generate a temporary file with the given content.  This file can be used by subprocesses

    On Windows, NamedTemporaryFiles can't be opened a second time, so we have to close it first (and delete it manually later)
    """
    from conda_lock.common import write_file

    tf = tempfile.NamedTemporaryFile(delete=False)
    try:
        tf.close()
        write_file(content, tf.name)
        yield pathlib.Path(tf.name)
    finally:
        os.unlink(tf.name)
