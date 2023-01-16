from __future__ import annotations

from conda_lock._vendor.poetry.layouts.layout import Layout
from conda_lock._vendor.poetry.layouts.src import SrcLayout


_LAYOUTS = {"src": SrcLayout, "standard": Layout}


def layout(name: str) -> type[Layout]:
    if name not in _LAYOUTS:
        raise ValueError("Invalid layout")

    return _LAYOUTS[name]
