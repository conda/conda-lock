from conda_lock import conda_solver
from conda_lock.lookup import DEFAULT_MAPPING_URL
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import VersionedDependency


def test_solve_conda_merges_duplicate_dependency_specs(monkeypatch) -> None:
    for depends in [
        ["python", "python >=3.10"],
        ["python >=3.10", "python"],
    ]:

        def solve_specs_for_arch(*args, **kwargs):
            return {
                "actions": {
                    "FETCH": [
                        {
                            "name": "python",
                            "version": "3.13.0",
                            "depends": [],
                            "url": "https://conda.anaconda.org/conda-forge/linux-64/python-3.13.0-0.conda",
                            "md5": "1",
                        },
                        {
                            "name": "babel",
                            "version": "2.18.0",
                            "depends": depends,
                            "url": "https://conda.anaconda.org/conda-forge/noarch/babel-2.18.0-pyhcf101f3_1.conda",
                            "md5": "2",
                        },
                    ],
                },
            }

        monkeypatch.setattr(conda_solver, "solve_specs_for_arch", solve_specs_for_arch)

        planned = conda_solver.solve_conda(
            conda="micromamba",
            specs={
                "babel": VersionedDependency(
                    name="babel",
                    version="",
                    manager="conda",
                    category="main",
                    extras=[],
                ),
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string("conda-forge")],
            mapping_url=DEFAULT_MAPPING_URL,
        )

        assert planned["babel"].dependencies["python"] == ">=3.10"
