#!/bin/bash
# Setup script to install conda-lock in editable mode from mounted source

set -e

if [ -d "/conda-lock-src" ]; then
    micromamba run -n base pip check
    echo "Setting up editable install from /conda-lock-src..."
    micromamba run -n base pip install --no-deps --editable /conda-lock-src
    micromamba run -n base pip check
    echo "Editable install complete!"
else
    echo "No /conda-lock-src found, using existing PyPI install"
fi
