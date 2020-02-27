import pkg_resources

from conda_lock.conda_lock import main


__all__ = ["main"]


try:
    __version__ = pkg_resources.get_distribution("conda_lock").version
except Exception:
    __version__ = "unknown"
