import pytest

from conda_lock import conda_solver
from conda_lock.errors import MetadataConsistencyError
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import VersionedDependency


@pytest.mark.parametrize("missing_dependency", [True])
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
