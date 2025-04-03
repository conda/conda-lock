#!/bin/bash

set -euo pipefail

if ! command -v vendoring &> /dev/null; then
  echo "vendoring not found on PATH. Please install vendoring with 'pip install vendoring'."
  exit 1
fi

if ! command -v dos2unix &> /dev/null; then
  echo "dos2unix not found on PATH. Please install dos2unix with 'apt-get install dos2unix' or 'brew install dos2unix'."
  exit 1
fi

if [ ! -f pyproject.toml ]; then
  echo "pyproject.toml not found in the current directory. Please run this script from the root of the project:"
  echo "  conda_lock/scripts/vendor_poetry/rerun_vendoring.sh"
  exit 1
fi

vendoring sync -vvv .

echo Fixing CRLF line endings...
dos2unix conda_lock/_vendor/poetry/core/_vendor/lark/grammars/*
dos2unix conda_lock/_vendor/poetry/core/_vendor/fastjsonschema/*
dos2unix conda_lock/_vendor/poetry/core/_vendor/lark/LICENSE

echo Downloading missing licenses...
for package in poetry poetry-core cleo; do
  curl -s "https://raw.githubusercontent.com/python-poetry/${package}/master/LICENSE" > "conda_lock/_vendor/${package}.LICENSE"
done
curl -s "https://raw.githubusercontent.com/conda/conda/master/LICENSE" > "conda_lock/_vendor/conda.LICENSE"

echo Removing duplicate licenses...
diff conda_lock/_vendor/conda/LICENSE.txt conda_lock/_vendor/conda/_vendor/frozendict/LICENSE.txt
rm conda_lock/_vendor/conda/LICENSE.txt
# This one is actually correct, but we downloaded it to poetry-core.LICENSE above.
diff conda_lock/_vendor/poetry_core.LICENSE conda_lock/_vendor/poetry-core.LICENSE
rm conda_lock/_vendor/poetry-core.LICENSE
# These are licenses for poetry_core's vendored 'packaging', not poetry_core itself.
diff conda_lock/_vendor/poetry/core/_vendor/packaging/LICENSE.APACHE conda_lock/_vendor/poetry_core.LICENSE.APACHE
diff conda_lock/_vendor/poetry/core/_vendor/packaging/LICENSE.BSD conda_lock/_vendor/poetry_core.LICENSE.BSD
rm conda_lock/_vendor/poetry_core.LICENSE.APACHE
rm conda_lock/_vendor/poetry_core.LICENSE.BSD

echo "Vendoring complete. Please commit the changes."
