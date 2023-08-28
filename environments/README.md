# How to install dev environment

First create the dev environment:

```
mamba env create -f environments/dev-environment.yaml
```

Then, add `conda-lock` and its dependencies (as specified in `pyproject.toml`):

```
pip install -e .
```
