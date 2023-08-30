from importlib.metadata import distribution

from conda_lock.conda_lock import main


__all__ = ["main"]


try:
    __version__ = distribution("conda_lock").version
except Exception:
    __version__ = "unknown"
