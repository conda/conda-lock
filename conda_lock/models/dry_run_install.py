from typing import TypedDict

from typing_extensions import NotRequired


class FetchAction(TypedDict):
    """
    FETCH actions include all the entries from the corresponding package's
    repodata.json
    """

    channel: str
    constrains: list[str] | None
    depends: list[str] | None
    fn: str
    md5: str
    sha256: str | None
    name: str
    subdir: str
    timestamp: int
    url: str
    version: str


class LinkAction(TypedDict, total=False):
    """LINK action shape varies by solver family.

    Empirically verified against real ``create --dry-run --json``
    captures (fixture files in ``tests/test-mamba-fixtures/``,
    literals in ``tests/support/fixtures.py``):

    - conda (captured: 26.5.2) and the Python ``mamba`` 1.x CLI
      (captured: mamba 1.5.12, which renders plans through conda's
      JSON printer) emit only the sparse conda-meta fields:
      ``base_url``, ``build_number``, ``build_string``, ``channel``,
      ``dist_name``, ``name``, ``platform``, ``version``. Dependency
      and artifact-identity information is absent and must be
      reconstructed from ``repodata_record.json`` on disk.
    - mamba 2.x / micromamba emit the full repodata record in LINK
      (``url``, ``fn``, ``md5``, ``sha256``, ``depends``,
      ``constrains``, ``subdir``, ``timestamp`` ...), so a FETCH can
      be synthesized without ever touching the package cache. This
      is a mamba-family trait, not a 2.6.0 feature: every micromamba
      captured (1.5.12, 2.0.8, 2.1.1, 2.5.0, 2.6.0, 2.8.1) emits the
      identical key set, differing only in the ``channel`` value
      (1.5.x uses the full channel URL, 2.x the bare name). The
      values are sourced from channel repodata at solve time.

    All fields are optional (``total=False``); callers must use ``.get()``
    and reason about which solver produced the action.
    """

    # Common to every solver captured
    channel: str
    name: str
    version: str
    # conda-meta-style fields (conda and Python mamba 1.x only)
    base_url: str
    dist_name: str
    platform: str
    # Full-repodata fields (mamba 2.x / micromamba; absent from
    # conda's sparse LINK)
    url: str
    fn: str
    md5: str
    sha256: str | None
    depends: list[str]
    constrains: list[str]
    subdir: str
    timestamp: int


class InstallActions(TypedDict):
    LINK: list[LinkAction]
    FETCH: list[FetchAction]
    UNLINK: NotRequired[list[LinkAction]]


class DryRunInstall(TypedDict):
    actions: InstallActions
