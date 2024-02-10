# Contributing

Thanks for helping to improve conda-lock! We appreciate your time and effort.

## How to install a dev environment

Of course, we use conda-lock to manage our development environment.

1. Get [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)

2. Install and activate the `conda-lock-dev` environment:

   ```shell
   micromamba env create --name=conda-lock-dev --category=main --category=dev --file=environments/conda-lock.yml
   micromamba activate conda-lock-dev
   ```

3. Install `conda-lock` in editable mode. This will also install its runtime
   dependencies as defined in `pyproject.toml`.

   ```shell
   pip install --no-deps --editable .
   ```

4. Check to ensure that your Python environment is consistent.

   ```shell
   pip check
   ```

5. Finally, while not strictly necessary, it's recommended to install pre-commit to reduce development friction.

   ```shell
   pre-commit install
   ```
