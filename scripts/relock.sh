#!/usr/bin/env bash

for pyver in "3.11" "3.14"; do
    PYTHON_VERSION=${pyver} ./scripts/update_lockfile.sh
done

./scripts/generate_pixi.sh
