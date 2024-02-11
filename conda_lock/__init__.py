from importlib.metadata import distribution

from conda_lock.conda_lock import main


__all__ = ["main"]


try:
    __version__ = distribution("conda_lock").version
except Exception:  # noqa: BLE001
    __version__ = "unknown"
