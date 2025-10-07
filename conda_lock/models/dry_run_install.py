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


class LinkAction(TypedDict):
    """
    LINK actions include only entries from conda-meta, notably missing
    dependency and constraint information
    """

    base_url: str
    channel: str
    dist_name: str
    name: str
    platform: str
    version: str


class InstallActions(TypedDict):
    LINK: list[LinkAction]
    FETCH: list[FetchAction]


class DryRunInstall(TypedDict):
    actions: InstallActions
