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

- The `state.delete_temp_paths` flag controls cleanup. Set it to False to preserve
  temporary paths for debugging and for inspecting intermediate files and directories
  created during the locking process.

Thread Safety:
- This module uses thread-local storage to manage state (`delete_temp_paths` and
  `_preserved_paths`), making it safe for concurrent use in multi-threaded applications
  and parallel test execution (e.g., pytest-xdist).

- Each thread maintains its own independent copy of the state, preventing interference
  between threads.

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
import threading

from collections.abc import Iterator
from contextlib import contextmanager


class _TempdirState:
    """
    State manager for temporary directory behavior.

    This class manages the configuration and tracking of temporary paths:

    - delete_temp_paths: A boolean attribute that controls whether temporary
      paths are deleted (True) or preserved (False). Can be toggled on or off
      as needed. Defaults to True.

    - _preserved_paths: A list for tracking temporary file and directory paths
      that are preserved for debugging (when delete_temp_paths is False). These
      paths are logged at program exit via _log_preserved_paths().

    Usage
    -----
    Access the global state instance via the module-level `state` object:

        from conda_lock import tempdir_manager as tm

        # Toggle deletion behavior
        tm.state.delete_temp_paths = False  # Preserve temp paths

        # Access preserved paths (for debugging/inspection)
        print(tm.state._preserved_paths)

    Implementation Notes
    --------------------
    State is stored using thread-local storage, ensuring that concurrent
    operations (different threads or pytest-xdist workers) don't interfere
    with each other.
    """

    def __init__(self) -> None:
        self._local = threading.local()

    @property
    def delete_temp_paths(self) -> bool:
        """
        Controls whether temporary paths are deleted (True) or preserved (False).

        This property is thread-local, meaning each thread has its own independent
        value. Defaults to True (delete paths).
        """
        if not hasattr(self._local, "delete_temp_paths"):
            self._local.delete_temp_paths = True
        return self._local.delete_temp_paths

    @delete_temp_paths.setter
    def delete_temp_paths(self, value: bool) -> None:
        self._local.delete_temp_paths = value

    @property
    def _preserved_paths(self) -> list[str]:
        """
        Thread-local list of paths that are being preserved for debugging.

        Each thread maintains its own list of preserved paths.
        """
        if not hasattr(self._local, "_preserved_paths"):
            self._local._preserved_paths = []
        return self._local._preserved_paths


# Global instance of the state manager.
# Access state via: state.delete_temp_paths, state._preserved_paths
state = _TempdirState()


def _track(path: str) -> None:
    """Track a preserved temporary directory for logging."""
    if len(state._preserved_paths) == 0:
        # Register exit handler on first preserved directory
        atexit.register(_log_preserved_paths)

    state._preserved_paths.append(path)


@contextmanager
def temporary_directory(
    prefix: str = "conda-lock-",
    dir: str | None = None,
    delete: bool | None = None,
) -> Iterator[str]:
    """
    Create a temporary directory honoring deletion behavior.

    If state.delete_temp_paths is True (default), the directory is cleaned up on context exit.
    If state.delete_temp_paths is False, the directory is preserved (and tracked).

    Parameters
    ----------
    prefix : str
        Prefix for the temporary directory name.
    dir : str | None
        Parent directory for the temporary directory.
    delete : bool | None
        If True, ensure the directory is deleted. If False, ensure it is not.
        If None, defer to the thread-local `state.delete_temp_paths` setting.

    Yields
    ------
    str
        Path to the temporary directory.
    """
    if delete is None:
        delete = state.delete_temp_paths
    if delete:
        # Use standard temporary directory with cleanup
        with tempfile.TemporaryDirectory(prefix=prefix, dir=dir) as tmp_dir:
            yield tmp_dir
    else:
        # Create a directory that won't be cleaned up
        tmp_dir = tempfile.mkdtemp(prefix=prefix, dir=dir)
        _track(tmp_dir)
        yield tmp_dir


def mkdtemp_with_cleanup(
    prefix: str = "conda-lock-",
    dir: str | None = None,
    delete: bool | None = None,
) -> str:
    """
    Create a temporary directory with optional cleanup at process exit.

    If state.delete_temp_paths is True, cleanup is registered via atexit.
    Otherwise, the directory is preserved and tracked for logging.

    Parameters
    ----------
    prefix : str
        Prefix for the temporary directory name.
    dir : str | None
        Parent directory for the temporary directory.
    delete : bool | None
        If True, ensure cleanup is registered. If False, ensure it is not.
        If None, defer to the thread-local `state.delete_temp_paths` setting.

    Returns
    -------
    str
        Path to the temporary directory.
    """
    path = tempfile.mkdtemp(prefix=prefix, dir=dir)

    if delete is None:
        delete = state.delete_temp_paths
    if delete:
        # Register cleanup at exit
        atexit.register(lambda: shutil.rmtree(path, ignore_errors=True))
    else:
        # Don't register cleanup; track for logging
        _track(path)

    return path


def _log_preserved_paths() -> None:
    """Log all preserved temporary files and directories."""
    if state._preserved_paths:
        print("=" * 60, file=sys.stderr)
        print("Preserved temporary paths:", file=sys.stderr)
        for path in state._preserved_paths:
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
def temporary_file_with_contents(
    content: str,
    *,
    prefix: str = "conda-lock-",
    dir: str | None = None,
    delete: bool | None = None,
) -> Iterator[pathlib.Path]:
    """Generate a temporary file with the given content.

    Use as a context manager; it yields a `pathlib.Path` to the created file.

    The file is created in a way that allows it to be re-opened by subprocesses
    on all platforms (including Windows). Deletion behavior follows the module's
    deletion policy:

    - If `state.delete_temp_paths` is True (default), the file is deleted when leaving
      this context.
    - If `state.delete_temp_paths` is False, the file is preserved and its path is
      tracked for visibility via `_track`.

    Parameters
    ----------
    content : str
        File content to write.
    prefix : str
        Prefix for the temporary filename.
    dir : str | None
        Directory in which to create the temporary file.
    delete : bool | None
        If True, ensure the file is deleted. If False, ensure it is not.
        If None, defer to the thread-local `state.delete_temp_paths` setting.

    Yields
    ------
    pathlib.Path
        Path to the created temporary file.
    """
    from conda_lock.common import write_file

    tf = tempfile.NamedTemporaryFile(prefix=prefix, dir=dir, delete=False)
    try:
        tf.close()
        write_file(content, tf.name)
        path_obj = pathlib.Path(tf.name)

        if delete is None:
            delete = state.delete_temp_paths
        if not delete:
            _track(tf.name)

        yield path_obj
    finally:
        if delete is None:
            delete = state.delete_temp_paths
        if delete:
            try:
                os.unlink(tf.name)
            except FileNotFoundError:
                pass
