
## Developing

1. Ensure that Conda, Mamba, and Micromamba are installed. Install [mambaforge](https://github.com/conda-forge/miniforge#mambaforge) if you're otherwise not sure which Conda distribution to pick.
2. `micromamba create --name=conda-lock-dev --category=main --category=dev --file=environments/conda-lock.yml`
3. `conda activate conda-lock-dev`
4. `pip install --no-deps --editable .`

Run the tests to ensure that everything is running correctly. Due to the nature of this project, it hits remote webservers regularly so some tests occasionally fail. This is a normal part of conda-lock development. If you're not sure if your env is borked or the remote webserver is just being flaky, run the tests again. If you're still not sure you can open an issue about.

5. `pytest`

Whilst not strictly necessary; the CI run using github actions will run pre-commit in order to reduce development friction you may want to install the pre-commit hooks:

6. `pre-commit install`
