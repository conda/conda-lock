"""Component tests for ``conda_lock.solver.graph_integrity``.

Forward-reachability of the planned conda package set, the
``CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE=1`` escape hatch, and the
no-reverse-propagation invariant.

These are component (not unit) tests because they exercise the full
``solve_conda`` orchestration path with a fake solver -- the orphan
guarantee is meaningful only after the dryrun has been parsed,
``apply_categories`` has run, and the graph integrity check fires.
The fake-solve indirection lets us pin specific dryrun shapes
(healthy ``python.depends=[pip]`` vs. broken
``python.depends=[]``) without standing up a real solver.
"""

# mypy: disable-error-code="arg-type,comparison-overlap"

from __future__ import annotations

import pytest

from conda_lock.conda_solver import solve_conda
from conda_lock.errors import OrphanLockedDependencyError
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import VersionedDependency


def _spec(name: str) -> VersionedDependency:
    return VersionedDependency(
        name=name, version="", manager="conda", category="main", extras=[]
    )


def test_solve_conda_accepts_pip_via_python_add_pip_dependency(monkeypatch):
    """Healthy conda/mamba dryruns are forward-reachable, including
    for ``pip``: both conda's ``add_pip_as_python_dependency`` (in
    ``conda.core.subdir_data``) and libmamba's repo-load injection
    mutate ``python``'s declared dependencies to include ``pip``,
    and libmamba additionally injects ``pip`` as an explicit root
    spec when the user requests ``python``. So a forward walk from
    the ``python`` root reaches ``pip`` cleanly without any
    reverse-propagation rescue. This is the *normal* shape -- not
    an orphan to be tolerated.
    """

    def fake_solve(*args, **kwargs):
        return {
            "actions": {
                "FETCH": [
                    {
                        "name": "python",
                        "version": "3.10",
                        # The post-injection shape: python declares pip.
                        "depends": ["pip"],
                        "url": "https://conda.example.com/python.conda",
                        "md5": "a" * 32,
                        "sha256": "b" * 64,
                        "channel": "conda-forge",
                        "fn": "python.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        "name": "pip",
                        "version": "25.2",
                        "depends": ["python"],
                        "url": "https://conda.example.com/pip.conda",
                        "md5": "c" * 32,
                        "sha256": "d" * 64,
                        "channel": "conda-forge",
                        "fn": "pip.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                ],
                "LINK": [],
            }
        }

    monkeypatch.setattr("conda_lock.conda_solver.solve_specs_for_arch", fake_solve)
    planned = solve_conda(
        conda="/dummy",
        specs={
            "python": VersionedDependency(
                name="python",
                version="",
                manager="conda",
                category="dev",
                extras=[],
            )
        },
        locked={},
        update=[],
        platform="linux-64",
        channels=[Channel.from_string("conda-forge")],
        mapping_url="dummy",
    )
    # No exception -- ``pip`` is reachable via ``python.depends``.
    # Forward walk also propagates the requested category, so ``pip``
    # ends up in ``dev`` without any reverse-prop rescue.
    assert planned["pip"].categories == {"dev"}
    assert planned["python"].categories == {"dev"}


def test_solve_conda_hard_fails_when_python_metadata_omits_pip(monkeypatch):
    """The pathological inverse: a dryrun in which ``python.depends``
    does *not* include ``pip``, but the solver still planned to
    install ``pip``. This is *not* the expected solver shape -- conda
    and libmamba both inject ``pip`` into ``python``'s dependencies at
    repodata-load time. An orphaned ``pip`` therefore indicates
    abnormal/broken solver metadata (e.g. a custom solver, an exotic
    channel, or corrupt repodata that erased the injection). The
    orphan check must hard-fail rather than silently inherit a
    category from a dependent.
    """

    def fake_solve(*args, **kwargs):
        return {
            "actions": {
                "FETCH": [
                    {
                        "name": "python",
                        "version": "3.10",
                        # Missing the ``add_pip_as_python_dependency``
                        # injection: empty depends. This is abnormal.
                        "depends": [],
                        "url": "https://conda.example.com/python.conda",
                        "md5": "a" * 32,
                        "sha256": "b" * 64,
                        "channel": "conda-forge",
                        "fn": "python.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        "name": "pip",
                        "version": "25.2",
                        "depends": ["python"],
                        "url": "https://conda.example.com/pip.conda",
                        "md5": "c" * 32,
                        "sha256": "d" * 64,
                        "channel": "conda-forge",
                        "fn": "pip.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                ],
                "LINK": [],
            }
        }

    monkeypatch.setattr("conda_lock.conda_solver.solve_specs_for_arch", fake_solve)
    with pytest.raises(OrphanLockedDependencyError) as excinfo:
        solve_conda(
            conda="/dummy",
            specs={
                "python": VersionedDependency(
                    name="python",
                    version="",
                    manager="conda",
                    category="dev",
                    extras=[],
                )
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
            mapping_url="dummy",
        )
    assert "pip" in str(excinfo.value)
    assert "unreachable" in str(excinfo.value)
    assert "CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE" in str(excinfo.value)


def test_solve_conda_envvar_demotes_orphan_to_warning(monkeypatch, caplog):
    """``CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE=1`` is the documented
    emergency escape: it converts the hard-fail into a loud WARNING
    and assigns orphans to ``main`` so they survive v1 serialization.
    This exists for implementation-bug emergencies and one-off
    recovery (a buggy custom solver, a missing dependency injection
    on an exotic channel, etc.) -- *not* as a supported configuration
    knob to silence orphans for normal solves, where the policy fix
    is to use a healthy solver/channel that injects ``pip`` properly
    or to explicitly request the orphaned package.
    """

    def fake_solve(*args, **kwargs):
        # Same pathological shape as the hard-fail test above:
        # ``python.depends`` is missing the standard ``pip``
        # injection. This is the kind of broken-graph scenario the
        # escape hatch exists for.
        return {
            "actions": {
                "FETCH": [
                    {
                        "name": "python",
                        "version": "3.10",
                        "depends": [],
                        "url": "https://conda.example.com/python.conda",
                        "md5": "a" * 32,
                        "sha256": "b" * 64,
                        "channel": "conda-forge",
                        "fn": "python.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        "name": "pip",
                        "version": "25.2",
                        "depends": ["python"],
                        "url": "https://conda.example.com/pip.conda",
                        "md5": "c" * 32,
                        "sha256": "d" * 64,
                        "channel": "conda-forge",
                        "fn": "pip.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                ],
                "LINK": [],
            }
        }

    monkeypatch.setattr("conda_lock.conda_solver.solve_specs_for_arch", fake_solve)
    monkeypatch.setenv("CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE", "1")

    with caplog.at_level("WARNING", logger="conda_lock.solver.graph_integrity"):
        planned = solve_conda(
            conda="/dummy",
            specs={
                "python": VersionedDependency(
                    name="python",
                    version="",
                    manager="conda",
                    category="dev",
                    extras=[],
                )
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
            mapping_url="dummy",
        )

    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE=1" in msgs
    assert "pip" in msgs
    # The warning must spell out the concrete category consequence:
    # promoting orphans to ``main`` means they install on every
    # invocation including ones that did NOT pass
    # ``--dev-dependencies``. A user who blindly enables the escape
    # hatch should not have to read the source to discover this.
    assert "main" in msgs
    assert "--dev-dependencies" in msgs or "-e <category>" in msgs
    # Orphans get assigned ``main`` so they survive v1 serialization.
    # The escape hatch produces a working but unusual lockfile, not a
    # silently-broken one -- but see the warning above for the
    # over-install consequence.
    assert planned["pip"].categories == {"main"}


def test_solve_conda_hard_fails_on_unrecoverable_orphan(monkeypatch):
    """If a planned package has no category *and* its declared
    ``.dependencies`` are empty, that's a real failure mode --
    typically the corrupt-cache scenario from conda/conda-lock#896.
    solve_conda must refuse rather than silently drop the package or
    paper over the failure by stuffing it into ``main``.
    """

    def fake_solve(*args, **kwargs):
        return {
            "actions": {
                "FETCH": [
                    {
                        "name": "libzlib",
                        "version": "1.3.2",
                        "depends": [],  # doesn't list zlib
                        "url": "https://conda.example.com/libzlib.conda",
                        "md5": "a" * 32,
                        "sha256": "b" * 64,
                        "channel": "conda-forge",
                        "fn": "libzlib.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        "name": "zlib",
                        "version": "1.3.2",
                        # Empty depends -- nothing for reverse propagation
                        # to inherit from. This is the corrupt-cache
                        # failure mode.
                        "depends": [],
                        "url": "https://conda.example.com/zlib.conda",
                        "md5": "c" * 32,
                        "sha256": "d" * 64,
                        "channel": "conda-forge",
                        "fn": "zlib.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                ],
                "LINK": [],
            }
        }

    monkeypatch.setattr("conda_lock.conda_solver.solve_specs_for_arch", fake_solve)
    with pytest.raises(OrphanLockedDependencyError) as excinfo:
        solve_conda(
            conda="/dummy",
            specs={"libzlib": _spec("libzlib")},
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
            mapping_url="dummy",
        )
    assert "zlib" in str(excinfo.value)
    assert "896" in str(excinfo.value)
    # Remediation leads with regenerate-from-sources; clearing the
    # cache alone wouldn't recover packages already lost during a
    # previous v1 serialization, so that mustn't be the primary advice.
    assert "regenerate the lockfile from sources" in str(excinfo.value)
    # The error names the envvar escape as a documented emergency
    # path.
    assert "CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE" in str(excinfo.value)


def test_solve_conda_orphan_via_dep_breakage_still_hard_fails(monkeypatch):
    """A more involved variant of the corrupt-cache shape: ``pytest``
    is requested as dev but its dependency ``iniconfig`` cannot be
    reached because something between them lost its declared
    ``depends``. This reproduces the #896 failure mode where a
    package's *dependent* (not the package itself) has empty
    ``depends``, breaking the forward walk. The orphan check must
    still hard-fail."""

    def fake_solve(*args, **kwargs):
        return {
            "actions": {
                "FETCH": [
                    {
                        "name": "python",
                        "version": "3.10",
                        "depends": [],
                        "url": "https://conda.example.com/python.conda",
                        "md5": "a" * 32,
                        "sha256": "b" * 64,
                        "channel": "conda-forge",
                        "fn": "python.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        "name": "pytest",
                        "version": "8",
                        "depends": [],
                        "url": "https://conda.example.com/pytest.conda",
                        "md5": "e" * 32,
                        "sha256": "f" * 64,
                        "channel": "conda-forge",
                        "fn": "pytest.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        # Orphan: depends on both python (requested as
                        # main) and pytest (requested as dev). Forward
                        # walk doesn't reach this package; reverse-prop
                        # would see {main, dev}. Refuse to assign.
                        "name": "spans_two_cats",
                        "version": "1",
                        "depends": ["python", "pytest"],
                        "url": "https://conda.example.com/spans_two_cats.conda",
                        "md5": "c" * 32,
                        "sha256": "d" * 64,
                        "channel": "conda-forge",
                        "fn": "spans_two_cats.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                ],
                "LINK": [],
            }
        }

    monkeypatch.setattr("conda_lock.conda_solver.solve_specs_for_arch", fake_solve)
    with pytest.raises(OrphanLockedDependencyError) as excinfo:
        solve_conda(
            conda="/dummy",
            specs={
                "python": VersionedDependency(
                    name="python",
                    version="",
                    manager="conda",
                    category="main",
                    extras=[],
                ),
                "pytest": VersionedDependency(
                    name="pytest",
                    version="",
                    manager="conda",
                    category="dev",
                    extras=[],
                ),
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
            mapping_url="dummy",
        )
    assert "spans_two_cats" in str(excinfo.value)


def test_solve_conda_passes_when_dependency_graph_is_intact(monkeypatch):
    """Sanity check -- with a well-formed dryrun (libzlib correctly
    declares zlib in its depends), no orphan is produced and solve_conda
    returns normally."""

    def fake_solve(*args, **kwargs):
        return {
            "actions": {
                "FETCH": [
                    {
                        "name": "libzlib",
                        "version": "1.3.2",
                        "depends": ["zlib"],
                        "url": "https://conda.example.com/libzlib.conda",
                        "md5": "a" * 32,
                        "sha256": "b" * 64,
                        "channel": "conda-forge",
                        "fn": "libzlib.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                    {
                        "name": "zlib",
                        "version": "1.3.2",
                        "depends": [],
                        "url": "https://conda.example.com/zlib.conda",
                        "md5": "c" * 32,
                        "sha256": "d" * 64,
                        "channel": "conda-forge",
                        "fn": "zlib.conda",
                        "subdir": "linux-64",
                        "timestamp": 1,
                    },
                ],
                "LINK": [],
            }
        }

    monkeypatch.setattr("conda_lock.conda_solver.solve_specs_for_arch", fake_solve)
    planned = solve_conda(
        conda="/dummy",
        specs={"libzlib": _spec("libzlib")},
        locked={},
        update=[],
        platform="linux-64",
        channels=[Channel.from_string("conda-forge")],
        mapping_url="dummy",
    )
    assert set(planned) == {"libzlib", "zlib"}
    assert all(p.categories for p in planned.values())
