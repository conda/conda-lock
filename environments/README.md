# Conda environment specifications

Our development dependencies are specified in [`dev-environment.yaml`](dev-environment.yaml).

The lockfile [`conda-lock.yml`](conda-lock.yml) is regularly updated using `conda-lock`
via the [`update-lockfile.yaml`](../.github/workflows/update-lockfile.yaml) GHA workflow.
In particular, the lockfile is generated based on the project dependencies specified in
[`pyproject.toml`](../pyproject.toml) the development dependencies specified in
[`dev-environment.yml`](dev-environment.yml).
