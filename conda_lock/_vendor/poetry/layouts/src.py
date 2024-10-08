from __future__ import annotations

from pathlib import Path

from conda_lock._vendor.poetry.layouts.layout import Layout


class SrcLayout(Layout):
    @property
    def basedir(self) -> Path:
        return Path("src")
