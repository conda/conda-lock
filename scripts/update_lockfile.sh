#!/usr/bin/env bash

if [[ ${PYTHON_VERSION} == "" ]]; then
    echo "You must specify the python version in the PYTHON_VERSION env var!"
    exit 1
fi

rm -f environments/conda-lock-python-${PYTHON_VERSION}.yaml
conda-lock \
    --file=environments/dev-environment.yaml \
    --file=environments/python-${PYTHON_VERSION}.yaml \
    --file=pyproject.toml \
    --lockfile=environments/conda-lock-python-${PYTHON_VERSION}.yaml
