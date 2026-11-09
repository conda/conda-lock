#!/usr/bin/env bash

conda-lock render-lock-spec --kind=pixi.toml --stdout \
  --file=environments/dev-environment.yaml \
  --file=pyproject.toml \
  --pixi-project-name=conda-lock \
  --editable conda-lock=. \
  > pixi.toml

pixi update
