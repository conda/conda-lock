from typing import TypedDict


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
    """LINK action shape varies by solver.

    - Conda and pre-2.6 mamba emit only the conda-meta-style fields:
      ``base_url``, ``channel``, ``dist_name``, ``name``, ``platform``,
      ``version``. Dependency and constraint information is absent and
      must be reconstructed from ``repodata_record.json`` on disk.
    - Mamba / micromamba 2.6.0+ emit the full repodata record in LINK
      (``url``, ``fn``, ``md5``, ``sha256``, ``depends``, ``constrains``,
      ``subdir``, ``timestamp`` ...), so a FETCH can be synthesized
      without ever touching the package cache.

    All fields are optional (``total=False``); callers must use ``.get()``
    and reason about which solver produced the action.
    """

    # Always present (conda + mamba)
    base_url: str
    channel: str
    dist_name: str
    name: str
    platform: str
    version: str
    # Mamba 2.6.0+ adds (and pre-2.6 mamba may include some of these):
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


class DryRunInstall(TypedDict):
    actions: InstallActions
