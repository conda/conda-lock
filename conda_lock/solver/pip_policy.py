"""Normalize unsolicited pip using verified, uninjected channel dependencies."""

import logging

from collections.abc import Iterable, Mapping
from typing import Any

from conda_lock.interfaces.vendored_conda import MatchSpec


def unrequested_pip_packages(
    records: Mapping[str, Mapping[str, Any]], requested: Iterable[str]
) -> set[str]:
    """Identify a complete orphan component explained by adding pip as a root.

    This is a diagnostic, not proof of injection: a lost genuine incoming edge
    can give the same result. Verify channel records before applying any omission.
    Incomplete graphs and unexplained orphans never qualify.
    """
    roots = set(requested)
    if "pip" not in records or "pip" in roots:
        return set()
    graph = {}
    for name, record in records.items():
        dependencies = {MatchSpec(spec).name for spec in record["depends"]}
        graph[name] = {dep for dep in dependencies if not dep.startswith("__")}
    # A missing root/edge might be what connects pip to the requested graph.
    # An update may restore it; until then we cannot justify deleting anything.
    if roots.difference(graph) or any(
        deps.difference(graph) for deps in graph.values()
    ):
        return set()

    def reachable(start: Iterable[str]) -> set[str]:
        visited: set[str] = set()
        pending = list(start)
        while pending:
            name = pending.pop()
            if name not in visited:
                visited.add(name)
                pending.extend(graph[name].difference(visited))
        return visited

    needed = reachable(roots)
    if "pip" in needed:
        return set()
    orphans = {
        name for name in graph if name not in needed and not name.startswith("__")
    }
    if orphans.difference(reachable(["pip"])):
        return set()
    return orphans


def omit_unrequested_pip(
    records: Mapping[str, Mapping[str, Any]], requested: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Omit an unwanted pip component from verified, uninjected channel records."""
    orphans = unrequested_pip_packages(records, requested)
    if orphans:
        logging.getLogger(__name__).info(
            "Omitting unrequested pip and its exclusive dependencies: %s",
            sorted(orphans),
        )
    return {
        name: dict(record) for name, record in records.items() if name not in orphans
    }
