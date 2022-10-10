# -*- coding: utf-8 -*-
import re
import subprocess

from collections import namedtuple
from typing import Any
from typing import Optional

from conda_lock._vendor.poetry.core.utils._compat import PY36
from conda_lock._vendor.poetry.core.utils._compat import WINDOWS
from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.utils._compat import decode


pattern_formats = {
    "protocol": r"\w+",
    "user": r"[a-zA-Z0-9_.-]+",
    "resource": r"[a-zA-Z0-9_.-]+",
    "port": r"\d+",
    "path": r"[\w~.\-/\\]+",
    "name": r"[\w~.\-]+",
    "rev": r"[^@#]+",
}

PATTERNS = [
    re.compile(
        r"^(git\+)?"
        r"(?P<protocol>https?|git|ssh|rsync|file)://"
        r"(?:(?P<user>{user})@)?"
        r"(?P<resource>{resource})?"
        r"(:(?P<port>{port}))?"
        r"(?P<pathname>[:/\\]({path}[/\\])?"
        r"((?P<name>{name}?)(\.git|[/\\])?)?)"
        r"([@#](?P<rev>{rev}))?"
        r"$".format(
            user=pattern_formats["user"],
            resource=pattern_formats["resource"],
            port=pattern_formats["port"],
            path=pattern_formats["path"],
            name=pattern_formats["name"],
            rev=pattern_formats["rev"],
        )
    ),
    re.compile(
        r"(git\+)?"
        r"((?P<protocol>{protocol})://)"
        r"(?:(?P<user>{user})@)?"
        r"(?P<resource>{resource}:?)"
        r"(:(?P<port>{port}))?"
        r"(?P<pathname>({path})"
        r"(?P<name>{name})(\.git|/)?)"
        r"([@#](?P<rev>{rev}))?"
        r"$".format(
            protocol=pattern_formats["protocol"],
            user=pattern_formats["user"],
            resource=pattern_formats["resource"],
            port=pattern_formats["port"],
            path=pattern_formats["path"],
            name=pattern_formats["name"],
            rev=pattern_formats["rev"],
        )
    ),
    re.compile(
        r"^(?:(?P<user>{user})@)?"
        r"(?P<resource>{resource})"
        r"(:(?P<port>{port}))?"
        r"(?P<pathname>([:/]{path}/)"
        r"(?P<name>{name})(\.git|/)?)"
        r"([@#](?P<rev>{rev}))?"
        r"$".format(
            user=pattern_formats["user"],
            resource=pattern_formats["resource"],
            port=pattern_formats["port"],
            path=pattern_formats["path"],
            name=pattern_formats["name"],
            rev=pattern_formats["rev"],
        )
    ),
    re.compile(
        r"((?P<user>{user})@)?"
        r"(?P<resource>{resource})"
        r"[:/]{{1,2}}"
        r"(?P<pathname>({path})"
        r"(?P<name>{name})(\.git|/)?)"
        r"([@#](?P<rev>{rev}))?"
        r"$".format(
            user=pattern_formats["user"],
            resource=pattern_formats["resource"],
            path=pattern_formats["path"],
            name=pattern_formats["name"],
            rev=pattern_formats["rev"],
        )
    ),
]


class GitError(RuntimeError):

    pass


class ParsedUrl:
    def __init__(
        self,
        protocol,  # type: Optional[str]
        resource,  # type: Optional[str]
        pathname,  # type: Optional[str]
        user,  # type: Optional[str]
        port,  # type: Optional[str]
        name,  # type: Optional[str]
        rev,  # type: Optional[str]
    ):
        self.protocol = protocol
        self.resource = resource
        self.pathname = pathname
        self.user = user
        self.port = port
        self.name = name
        self.rev = rev

    @classmethod
    def parse(cls, url):  # type: (str) -> ParsedUrl
        for pattern in PATTERNS:
            m = pattern.match(url)
            if m:
                groups = m.groupdict()
                return ParsedUrl(
                    groups.get("protocol"),
                    groups.get("resource"),
                    groups.get("pathname"),
                    groups.get("user"),
                    groups.get("port"),
                    groups.get("name"),
                    groups.get("rev"),
                )

        raise ValueError('Invalid git url "{}"'.format(url))

    @property
    def url(self):  # type: () -> str
        return "{}{}{}{}{}".format(
            "{}://".format(self.protocol) if self.protocol else "",
            "{}@".format(self.user) if self.user else "",
            self.resource,
            ":{}".format(self.port) if self.port else "",
            "/" + self.pathname.lstrip(":/"),
        )

    def format(self):  # type: () -> str
        return self.url

    def __str__(self):  # type: () -> str
        return self.format()


GitUrl = namedtuple("GitUrl", ["url", "revision"])


_executable = None


def executable():
    global _executable

    if _executable is not None:
        return _executable

    if WINDOWS and PY36:
        # Finding git via where.exe
        where = "%WINDIR%\\System32\\where.exe"
        paths = decode(
            subprocess.check_output([where, "git"], shell=True, encoding="oem")
        ).split("\n")
        for path in paths:
            if not path:
                continue

            path = Path(path.strip())
            try:
                path.relative_to(Path.cwd())
            except ValueError:
                _executable = str(path)

                break
    else:
        _executable = "git"

    if _executable is None:
        raise RuntimeError("Unable to find a valid git executable")

    return _executable


def _reset_executable():
    global _executable

    _executable = None


class GitConfig:
    def __init__(self, requires_git_presence=False):  # type: (bool) -> None
        self._config = {}

        try:
            config_list = decode(
                subprocess.check_output(
                    [executable(), "config", "-l"], stderr=subprocess.STDOUT
                )
            )

            m = re.findall("(?ms)^([^=]+)=(.*?)$", config_list)
            if m:
                for group in m:
                    self._config[group[0]] = group[1]
        except (subprocess.CalledProcessError, OSError):
            if requires_git_presence:
                raise

    def get(self, key, default=None):  # type: (Any, Optional[Any]) -> Any
        return self._config.get(key, default)

    def __getitem__(self, item):  # type: (Any) -> Any
        return self._config[item]


class Git:
    def __init__(self, work_dir=None):  # type: (Optional[Path]) -> None
        self._config = GitConfig(requires_git_presence=True)
        self._work_dir = work_dir

    @classmethod
    def normalize_url(cls, url):  # type: (str) -> GitUrl
        parsed = ParsedUrl.parse(url)

        formatted = re.sub(r"^git\+", "", url)
        if parsed.rev:
            formatted = re.sub(r"[#@]{}$".format(parsed.rev), "", formatted)

        altered = parsed.format() != formatted

        if altered:
            if re.match(r"^git\+https?", url) and re.match(
                r"^/?:[^0-9]", parsed.pathname
            ):
                normalized = re.sub(r"git\+(.*:[^:]+):(.*)", "\\1/\\2", url)
            elif re.match(r"^git\+file", url):
                normalized = re.sub(r"git\+", "", url)
            else:
                normalized = re.sub(r"^(?:git\+)?ssh://", "", url)
        else:
            normalized = parsed.format()

        return GitUrl(re.sub(r"#[^#]*$", "", normalized), parsed.rev)

    @property
    def config(self):  # type: () -> GitConfig
        return self._config

    def clone(self, repository, dest):  # type: (str, Path) -> str
        self._check_parameter(repository)

        return self.run("clone", "--recurse-submodules", "--", repository, str(dest))

    def checkout(self, rev, folder=None):  # type: (str, Optional[Path]) -> str
        args = []
        if folder is None and self._work_dir:
            folder = self._work_dir

        if folder:
            args += [
                "--git-dir",
                (folder / ".git").as_posix(),
                "--work-tree",
                folder.as_posix(),
            ]

        self._check_parameter(rev)

        args += ["checkout", rev]

        return self.run(*args)

    def rev_parse(self, rev, folder=None):  # type: (str, Optional[Path]) -> str
        args = []
        if folder is None and self._work_dir:
            folder = self._work_dir

        if folder:
            args += [
                "--git-dir",
                (folder / ".git").as_posix(),
                "--work-tree",
                folder.as_posix(),
            ]

        self._check_parameter(rev)

        # We need "^0" (an alternative to "^{commit}") to ensure that the
        # commit SHA of the commit the tag points to is returned, even in
        # the case of annotated tags.
        #
        # We deliberately avoid the "^{commit}" syntax itself as on some
        # platforms (cygwin/msys to be specific), the braces are interpreted
        # as special characters and would require escaping, while on others
        # they should not be escaped.
        args += ["rev-parse", rev + "^0"]

        return self.run(*args)

    def get_ignored_files(self, folder=None):  # type: (Optional[Path]) -> list
        args = []
        if folder is None and self._work_dir:
            folder = self._work_dir

        if folder:
            args += [
                "--git-dir",
                (folder / ".git").as_posix(),
                "--work-tree",
                folder.as_posix(),
            ]

        args += ["ls-files", "--others", "-i", "--exclude-standard"]
        output = self.run(*args)

        return output.strip().split("\n")

    def remote_urls(self, folder=None):  # type: (Optional[Path]) -> dict
        output = self.run(
            "config", "--get-regexp", r"remote\..*\.url", folder=folder
        ).strip()

        urls = {}
        for url in output.splitlines():
            name, url = url.split(" ", 1)
            urls[name.strip()] = url.strip()

        return urls

    def remote_url(self, folder=None):  # type: (Optional[Path]) -> str
        urls = self.remote_urls(folder=folder)

        return urls.get("remote.origin.url", urls[list(urls.keys())[0]])

    def run(self, *args, **kwargs):  # type: (*Any, **Any) -> str
        folder = kwargs.pop("folder", None)
        if folder:
            args = (
                "--git-dir",
                (folder / ".git").as_posix(),
                "--work-tree",
                folder.as_posix(),
            ) + args

        return decode(
            subprocess.check_output(
                [executable()] + list(args), stderr=subprocess.STDOUT
            )
        ).strip()

    def _check_parameter(self, parameter):  # type: (str) -> None
        """
        Checks a git parameter to avoid unwanted code execution.
        """
        if parameter.strip().startswith("-"):
            raise GitError("Invalid Git parameter: {}".format(parameter))
