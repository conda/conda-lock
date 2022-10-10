from typing import TYPE_CHECKING
from typing import Optional
from typing import Union

from conda_lock._vendor.poetry.core.utils._compat import Path

from .builders.sdist import SdistBuilder
from .builders.wheel import WheelBuilder


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.poetry import Poetry  # noqa


class Builder:
    _FORMATS = {
        "sdist": SdistBuilder,
        "wheel": WheelBuilder,
    }

    def __init__(self, poetry):  # type: ("Poetry") -> None
        self._poetry = poetry

    def build(
        self, fmt, executable=None
    ):  # type: (str, Optional[Union[str, Path]]) -> None
        if fmt in self._FORMATS:
            builders = [self._FORMATS[fmt]]
        elif fmt == "all":
            builders = self._FORMATS.values()
        else:
            raise ValueError("Invalid format: {}".format(fmt))

        for builder in builders:
            builder(self._poetry, executable=executable).build()
