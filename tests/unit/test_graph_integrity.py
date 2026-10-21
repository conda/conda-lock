import pytest

from conda_lock import conda_solver
from conda_lock.errors import MetadataConsistencyError
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import VersionedDependency
from conda_lock.solver.graph_integrity import (
    check_dependency_constraints,
    packages_requiring_repair,
)


MALFORMED_SPEC = "pillow-heif >=0.10.0,<1.0.0<py312|>=0.13.0,<1.0.0>=py312"


@pytest.mark.parametrize(
    "check", [check_dependency_constraints, packages_requiring_repair]
)
def test_malformed_optional_constraints_warn_but_do_not_block_locks(check, caplog):
    records = [
        {"name": "willow", "fn": "willow-1.8.0-0.conda", "constrains": [MALFORMED_SPEC]}
    ]
    check(records)
    assert "Ignoring malformed optional constraint" in caplog.text
    assert "willow" in caplog.text
    assert MALFORMED_SPEC in caplog.text


@pytest.mark.parametrize(
    "boundary", ["final", "repair", "prune", "replace", "serialize"]
)
def test_malformed_required_dependencies_have_recovery_at_every_boundary(boundary):
    records = {
        "root": {"name": "root", "fn": "root-1-0.conda", "depends": [MALFORMED_SPEC]}
    }
    with pytest.raises(
        MetadataConsistencyError, match="Cannot parse required dependency"
    ) as error:
        if boundary == "final":
            check_dependency_constraints(list(records.values()))
        elif boundary == "repair":
            packages_requiring_repair(list(records.values()))
        elif boundary == "prune":
            conda_solver._unrequested_packages(records, ["root"])
        elif boundary == "replace":
            conda_solver._packages_requiring_replacement(records, {"leaf"})
        else:
            conda_solver._dependency_versions([MALFORMED_SPEC])
    assert "channel maintainer" in str(error.value)


@pytest.mark.parametrize("missing_dependency", [False, True])
def test_incomplete_plan_fails_before_packages_can_be_dropped(
    monkeypatch, missing_dependency
):
    records = [
        {
            "name": "root",
            "version": "1",
            "depends": ["missing"] if missing_dependency else [],
            "url": "https://example.org/c/linux-64/root-1-0.conda",
            "md5": "1",
        },
        {
            "name": "unreachable",
            "version": "1",
            "depends": [],
            "url": "https://example.org/c/linux-64/unreachable-1-0.conda",
            "md5": "2",
        },
    ]
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kwargs: {"actions": {"FETCH": records}},
    )
    monkeypatch.setattr(
        conda_solver,
        "verified_channel_records",
        lambda conda, platform, records, **kwargs: {r["name"]: r for r in records},
    )
    message = "missing required packages" if missing_dependency else "have no category"
    with pytest.raises(MetadataConsistencyError, match=message):
        conda_solver.solve_conda(
            conda="micromamba",
            specs={
                "root": VersionedDependency(name="root", version="*", category="dev")
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("https://example.org/c")],
            mapping_url="unused",
        )


@pytest.mark.parametrize("field", ["depends", "constrains"])
def test_raw_build_constraints_are_checked_before_version_only_serialization(field):
    from conda_lock.solver.graph_integrity import check_dependency_constraints

    records = [
        {
            "name": "root",
            "version": "1",
            "fn": "root-1-0.conda",
            field: ["blas * *openblas"],
        },
        {"name": "blas", "version": "1", "fn": "blas-1-0_mkl.conda"},
    ]
    with pytest.raises(MetadataConsistencyError, match="does not satisfy"):
        check_dependency_constraints(records)
    records[1]["fn"] = "blas-1-0_openblas.conda"
    check_dependency_constraints(records)


def test_final_verification_restores_hidden_edge_and_main_category(monkeypatch):
    from copy import deepcopy

    from tests.unit.test_pip_categories import records_for

    canonical = records_for(
        {"main-root": ["shared"], "dev-root": ["shared"], "shared": []}
    )
    returned = deepcopy(canonical)
    returned["main-root"]["depends"] = []
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kwargs: {"actions": {"FETCH": list(returned.values())}},
    )
    queries = []

    def query(*args, **kwargs):
        queries.append(args[2])
        return list(canonical.values())

    monkeypatch.setattr(
        "conda_lock.solver.channel_metadata.query_channel_records", query
    )
    result = conda_solver.solve_conda(
        conda="solver",
        platform="linux-64",
        channels=[],
        locked={},
        update=[],
        specs={
            name: VersionedDependency(name=name, version="*", category=category)
            for name, category in [("main-root", "main"), ("dev-root", "dev")]
        },
        mapping_url="unused",
    )
    assert len(queries) == 1
    assert set(result) == set(returned)  # Metadata refresh preserves selection.
    assert result["main-root"].dependencies == {"shared": ""}
    assert result["shared"].categories == {"main"}
    assert returned["main-root"]["depends"] == []


@pytest.mark.parametrize(
    "spec, expected",
    [("leaf >=2", {"leaf"}), ("leaf >=1", set()), ("absent", {"absent"})],
)
def test_pre_solve_check_identifies_missing_or_incompatible_dependencies(
    spec, expected
):
    from conda_lock.solver.graph_integrity import packages_requiring_repair

    records = [
        {
            "name": "root",
            "fn": "root-1-0.conda",
            "depends": [spec],
            "constrains": ["optional >=99"],
        },
        {"name": "leaf", "version": "1", "fn": "leaf-1-0.conda"},
    ]
    assert packages_requiring_repair(records) == expected
