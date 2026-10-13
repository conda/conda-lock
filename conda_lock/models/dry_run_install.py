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

    Captures in ``tests/test-mamba-fixtures/`` illustrate two shapes:

    - Conda 26.5.2 and Python Mamba 1.5.12 emit sparse identity fields.
      Dependencies must be recovered from verified channel metadata.
    - Mamba 2.6.0 and micromamba 1.5.12 include enough metadata to synthesize
      a FETCH directly. Completeness does not prove channel provenance:
      offline Mamba may obtain these fields from extracted package records
      when cached channel repodata is absent.

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
