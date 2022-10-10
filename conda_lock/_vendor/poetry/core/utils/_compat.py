import sys

from typing import AnyStr
from typing import List
from typing import Optional
from typing import Union

import six.moves.urllib.parse as urllib_parse


urlparse = urllib_parse


try:  # Python 2
    long = long
    unicode = unicode
    basestring = basestring
except NameError:  # Python 3
    long = int
    unicode = str
    basestring = str


PY2 = sys.version_info[0] == 2
PY34 = sys.version_info >= (3, 4)
PY35 = sys.version_info >= (3, 5)
PY36 = sys.version_info >= (3, 6)
PY37 = sys.version_info >= (3, 7)

WINDOWS = sys.platform == "win32"

if PY2:
    import pipes

    shell_quote = pipes.quote
else:
    import shlex

    shell_quote = shlex.quote

if PY35:
    from pathlib import Path  # noqa
else:
    from pathlib2 import Path  # noqa

if not PY36:
    from collections import OrderedDict  # noqa
else:
    OrderedDict = dict


try:
    FileNotFoundError
except NameError:
    FileNotFoundError = IOError  # noqa


def decode(
    string, encodings=None
):  # type: (Union[AnyStr, unicode], Optional[str]) -> Union[str, bytes]
    if not PY2 and not isinstance(string, bytes):
        return string

    if PY2 and isinstance(string, unicode):
        return string

    encodings = encodings or ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        try:
            return string.decode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return string.decode(encodings[0], errors="ignore")


def encode(
    string, encodings=None
):  # type: (AnyStr, Optional[str]) -> Union[str, bytes]
    if not PY2 and isinstance(string, bytes):
        return string

    if PY2 and isinstance(string, str):
        return string

    encodings = encodings or ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        try:
            return string.encode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return string.encode(encodings[0], errors="ignore")


def to_str(string):  # type: (AnyStr) -> str
    if isinstance(string, str) or not isinstance(string, (unicode, bytes)):
        return string

    if PY2:
        method = "encode"
    else:
        method = "decode"

    encodings = ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        try:
            return getattr(string, method)(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return getattr(string, method)(encodings[0], errors="ignore")


def list_to_shell_command(cmd):  # type: (List[str]) -> str
    executable = cmd[0]

    if " " in executable:
        executable = '"{}"'.format(executable)
        cmd[0] = executable

    return " ".join(cmd)
