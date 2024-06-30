from __future__ import annotations

from conda_lock._vendor.poetry.core.masonry.builders.sdist import SdistBuilder
from conda_lock._vendor.poetry.core.masonry.builders.wheel import WheelBuilder

from conda_lock._vendor.poetry.masonry.builders.editable import EditableBuilder


__all__ = ["BUILD_FORMATS", "EditableBuilder"]


# might be extended by plugins
BUILD_FORMATS = {
    "sdist": SdistBuilder,
    "wheel": WheelBuilder,
}
