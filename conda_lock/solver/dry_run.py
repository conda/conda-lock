"""Solver dryrun normalization.

Conda's ``--dry-run --json`` output is the protocol that conda-lock
consumes from conda/mamba/micromamba. This module owns translating
that output into a uniform shape with one ``FETCH`` per planned
package, regardless of whether the underlying solver returned
rich-LINK actions (mamba 2.6.0+), sparse-LINK actions (older mamba
or conda), or already-complete FETCH actions.
"""

from typing import cast

from conda_lock.models.dry_run_install import FetchAction, LinkAction


_FETCH_KEYS_FROM_LINK: tuple[str, ...] = (
    "channel",
    "depends",
    "fn",
    "md5",
    "name",
    "subdir",
    "timestamp",
    "url",
    "version",
)


def link_action_as_fetch(link_action: LinkAction) -> FetchAction | None:
    """Reuse a LINK action's metadata as a FETCH action when complete.

    Mamba/micromamba 2.6.0 returns LINK entries that already include every
    repodata field we need (``url``, ``fn``, ``md5``, ``sha256``,
    ``depends``, ``constrains``, ...). When that is the case we don't need
    to crack open ``repodata_record.json`` on disk.

    Synthesis is rejected unless the LINK has every field that the
    downstream code reads from a FETCH. Critically we require ``depends``
    to be present *and* a list, otherwise an absent or null value would
    silently erase a package's runtime dependencies.
    """
    for key in _FETCH_KEYS_FROM_LINK:
        if key not in link_action or link_action[key] is None:  # type: ignore[literal-required]
            return None
    if not isinstance(link_action["depends"], list):
        return None
    fetch = cast(
        FetchAction,
        {key: link_action[key] for key in _FETCH_KEYS_FROM_LINK},  # type: ignore[literal-required]
    )
    fetch["sha256"] = link_action.get("sha256")
    constrains = link_action.get("constrains")
    fetch["constrains"] = constrains if isinstance(constrains, list) else []
    return fetch
