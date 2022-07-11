
## Developing

For the most up-to-date instructions see the github actions [test.yml workflow](./github/workflows.test.yml)

1. Install conda and mamba
1. `mamba create -n test pip pytest-cov pytest-xdist pip`
1. `conda activate test`
1. `python -m pip install -r requirements.txt`
1. `python -m pip install -r requirements-dev.txt`
1. `pip install -e . --no-deps --force-reinstall`
1. `pytest -n auto -vrsx --cov=conda_lock tests`
Whilst not strictly necessary; the CI run using github actions will run pre-commit -- so in order to reduce development friction you may want to install and activate[ pre-commit
](https://pre-commit.com/)
1. `pre-commit install`

