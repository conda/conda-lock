"""Graph-integrity invariants for solved conda plans.

After ``apply_categories`` walks forward from each requested input
spec, every planned package should have at least one category. A
package without any category is an orphan: the lockfile dependency
graph cannot explain why this package is in the plan.

Orphans are *not* tolerated. They would silently vanish from the v1
lockfile output (which emits one entry per category, so a package
with no category produces zero entries) and the resulting
environment would install fewer packages than the solver actually
planned. ``assert_no_orphaned_conda_packages`` is the guard that
prevents that.

This module owns the entire orphan policy in one place:

- the definition of an orphaned planned package;
- why we do **not** reverse-propagate categories (would hide the
  broken-graph signal in single-category projects, and would
  launder dev-only solver artifacts into main installs);
- why ``pip`` is not normally an orphan
  (``add_pip_as_python_dependency`` in conda; equivalent
  injection in libmamba), so an orphaned ``pip`` indicates
  abnormal solver metadata rather than expected behavior;
- the ``CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE=1`` escape hatch and
  the over-install consequence of promoting orphans to ``main``.

The orphan check is one of the central invariants of the
lockfile, not a local cleanup step; it lives here so a future
reader sees that on first opening the file.
"""

import logging
import os

from conda_lock.errors import OrphanLockedDependencyError
from conda_lock.lockfile.v2prelim.models import LockedDependency


logger = logging.getLogger(__name__)


_ORPHAN_ESCAPE_ENVVAR = "CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE"


def assert_no_orphaned_conda_packages(
    planned: dict[str, LockedDependency], platform: str
) -> None:
    """Raise ``OrphanLockedDependencyError`` if any planned package is
    unreachable from the requested input specs.

    Called after ``apply_categories``: any conda entry still without a
    category at this point was not reached by the forward walk from
    the requested roots through declared ``.dependencies`` edges.
    That is the broken-graph signal we hard-fail on.

    No reverse-propagation. ``apply_categories``' forward walk is the
    only categorization path. The reasons are policy-level,
    independent of whether the orphan happens to be a
    "solver auto-install" like ``pip``:

    - Reverse-prop hides the broken-graph signal. In single-category
      projects (only ``main`` requested), every orphan with any
      categorized dependency would silently inherit ``main`` and the
      hard-fail would never fire on real conda/conda-lock#896-style
      breakage.
    - "P depends on X (main)" does not prove "main needs P"; it
      only correlates. A permissive variant would launder dev-only
      solver artifacts into main installs.

    In a healthy conda/mamba dryrun this should never fire even for
    ``pip``: both ``conda.core.subdir_data.add_pip_as_python_dependency``
    and libmamba's repo-load injection mutate ``python``'s declared
    dependencies to include ``pip`` at metadata-load time, and
    libmamba's install API additionally injects ``pip`` as an
    explicit root request whenever the user asks for ``python``. So
    a forward walk from ``python`` reaches ``pip`` in normal
    operation, and an orphaned ``pip`` is a sign of broken solver
    metadata rather than the expected shape.

    Escape hatch: ``CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE=1`` demotes
    the hard-fail to a loud WARNING and assigns orphans to ``main``
    so they survive v1 serialization. This is intentionally ugly
    and not documented as a stable interface; it exists for
    implementation-bug emergencies and one-off recovery (a buggy
    custom solver, missing dependency injection on an exotic
    channel, etc.). Promoting orphans to ``main`` will cause every
    ``conda-lock install`` invocation -- including ones that do
    NOT pass ``--dev-dependencies`` or ``-e <category>`` -- to
    install them, potentially over-installing dev-only solver
    artifacts into production environments.
    """
    orphans = sorted(name for name, dep in planned.items() if not dep.categories)
    if not orphans:
        return

    message = (
        f"{len(orphans)} planned conda package(s) on platform "
        f"{platform} are unreachable from the requested input "
        f"specs through declared `dependencies` edges: {orphans}. "
        f"The lockfile dependency graph is broken: these "
        f"packages would silently vanish from the v1 lockfile "
        f"output (which emits one entry per category, so a "
        f"package with no category produces zero entries) and "
        f"the resulting environment would install fewer "
        f"packages than the solver actually planned.\n\n"
        f"In a healthy conda/mamba dryrun every planned package "
        f"is forward-reachable from some requested spec. Even "
        f"``pip`` -- often described as a 'solver auto-install' "
        f"-- is normally reachable because conda and libmamba "
        f"both inject ``pip`` into ``python``'s declared "
        f"dependencies at repodata-load time "
        f"(``add_pip_as_python_dependency``). An orphan therefore "
        f"indicates either: (1) corrupt ``repodata_record.json`` "
        f"metadata from mamba/micromamba 2.1.1-2.3.3 (see "
        f"conda/conda-lock#896 / mamba-org/mamba#4110) leaving "
        f"the package -- or one of its dependents -- with empty "
        f"``depends``, breaking the forward walk; or (2) a "
        f"non-standard solver / channel / metadata source that "
        f"omits the usual dependency injections.\n\n"
        f"To resolve: regenerate the lockfile from sources on a "
        f"known-clean cache (`mamba clean -a` then `conda-lock "
        f"lock -f <your sources> ...`), or add the orphaned "
        f"package(s) as explicit input specs in the relevant "
        f"category. Re-running ``--update`` against the same "
        f"input lockfile after only clearing the local cache "
        f"will not recover packages that already vanished "
        f"during a previous v1 serialization.\n\n"
        f"Emergency escape hatch: set "
        f"``{_ORPHAN_ESCAPE_ENVVAR}=1`` to demote "
        f"this error to a warning and continue. Orphaned "
        f"packages will be assigned category ``main`` so they "
        f"survive v1 serialization. WARNING: assigning to "
        f"``main`` means a dev-only solver artifact (something "
        f"the solver pulled in only because a dev-category root "
        f"asked for it) will be installed by every "
        f"``conda-lock install`` invocation, including ones "
        f"that did NOT pass ``--dev-dependencies`` or "
        f"``-e <category>``. This can over-install dev-only "
        f"packages into production environments. The escape "
        f"hatch is a band-aid for implementation-bug "
        f"emergencies, not a supported configuration -- use it "
        f"only if you understand the consequences and have "
        f"verified that promoting the orphans to ``main`` is "
        f"acceptable for your install paths."
    )
    if os.environ.get(_ORPHAN_ESCAPE_ENVVAR) == "1":
        logger.warning(
            "%s=1 set; demoting orphaned-lockfile error to a "
            "warning and promoting orphans to category 'main'. "
            "This will cause every ``conda-lock install`` "
            "invocation -- including ones that do NOT pass "
            "``--dev-dependencies`` or ``-e <category>`` -- to "
            "install these packages, which may include dev-only "
            "solver artifacts. %s",
            _ORPHAN_ESCAPE_ENVVAR,
            message,
        )
        for name in orphans:
            planned[name].categories.add("main")
        return
    raise OrphanLockedDependencyError(message)
