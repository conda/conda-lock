
## Developing

For the most up-to-date instructions see the github actions [test.yml workflow](./github/workflows.test.yml)

1. Ensure conda and mamba are installed. Install [mambaforge](https://github.com/conda-forge/miniforge#mambaforge) if you're otherwise not sure which one to pick.
2. `mamba create -n conda-lock-dev pip pytest-cov pytest-xdist`
3. `conda activate conda-lock-dev`
4. `python -m pip install -r requirements-dev.txt`
5. `pip install -e . --no-deps --force-reinstall`

Run the tests to ensure that everything is running correctly. Due to the nature of this project, it hits remote webservers regularly so some tests occasionally fail. This is a normal part of conda-lock development. If you're not sure if your env is borked or the remote webserver is just being flaky, run the tests again. If you're still not sure you can open an issue about.

7. `pytest`

Whilst not strictly necessary; the CI run using github actions will run pre-commit in order to reduce development friction you may want to install the pre-commit hooks:

8. `pre-commit install`

