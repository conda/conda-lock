# How to install dev environment

* First install dev dependencies:

  ```
  mamba env create -f environments/dev-environment.yaml
  mamba activate conda-lock-dev
  ```

* Then, install `conda-lock` in editable mode. This will also install its runtime
  dependencies as defined in `pyproject.toml`.

  ```
  pip install --editable .
  ```
