import sys


try:
    from pathlib import Path
except ImportError:
    # noinspection PyUnresolvedReferences
    from pathlib2 import Path

__version__ = "1.0.8"

__vendor_site__ = (Path(__file__).parent / "_vendor").as_posix()

if __vendor_site__ not in sys.path:
    sys.path.insert(0, __vendor_site__)
