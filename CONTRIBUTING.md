
## Developing

For the most up-to-date instructions see the github actions [test.yml workflow](./github/workflows.test.yml)

1. Install conda and mamba
1. mamba create -n test pip pytest-cov pytest-xdist pip
1. conda activate test
1. python -m pip install -r requirements.txt
1. python -m pip install -r requirements-dev.txt
1. pip install -e . --no-deps --force-reinstall
1. pytest -n auto -vrsx --cov=conda_lock tests