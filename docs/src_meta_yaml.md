# meta.yaml

[Conda build][condabuild] defines package recipes using the [meta.yaml][metayaml] format.

Conda-lock will attempt to make an educated guess at the desired environment spec in a meta.yaml.

This is **not** guaranteed to work for complex recipes with many selectors and outputs or complex use of jinja templates.

For multi-output recipes, conda-lock will fuse all the dependencies together.  If that doesn't work for your case fall back to specifying the specification as an [environment.yml](/src_environment_yml)

```{.yaml title="meta.yaml"}

{% set version = "1.0.5" %}

package:
  name: foo
  version: {{ version }}

build:
  number: 0
  script:
    - export PYTHONUNBUFFERED=1  # [ppc64le]
    - {{ PYTHON }} -m pip install --no-deps --ignore-installed .
  skip: True  # [py2k]

requirements:
  build:
    - {{ compiler('c') }}
    - {{ compiler('cxx') }}
  host:
    - python
    - pip
    - cython >=0.28.2
    - numpy
  run:
    - python
    - {{ pin_compatible('numpy') }}
    - python-dateutil >=2.6.1
    - pytz >=2017.2
    - zlib     # [unix]

test:
  requires:
    - pytest
```

## Categories

- `build` requirements are ignored
- `host` and `run` dependencies are treated as **main**
- `test.requires` dependencies are treated as **dev**

By default conda-lock will include dev dependencies in the specification of the lock (if the files that the lock
is being built from support them).  This can be disabled easily

```sh
conda-lock --no-dev-dependencies --file meta.yaml
```

## Extensions

### Channel specification

Since a meta.yaml doesn't contain channel information we make use of the following `extra` key to specify channels

```yaml
extra:
  channels:
    - conda-forge
    - defaults
```

[conda]: https://docs.conda.io/projects/conda
[condabuild]: https://docs.conda.io/projects/condabuild
[metayaml]: https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html
