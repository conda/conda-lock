# Docker

In order to use conda-lock in a docker-style context you want to add the lockfile to the
docker container.  This avoids the case where the `conda install xyz` statement is cached.

Given a file tree like

```
  Dockerfile
  environment.yaml
* conda-linux-64.lock
```

You want a dockerfile that is structured something similar to this

```Dockerfile
# Dockerfile

# -----------------
# Builder container
# -----------------
FROM continuumio/miniconda:latest as builder

COPY conda-linux-64.lock /locks/conda-linux-64.lock
RUN conda create -p /opt/env --copy --file /locks/conda-linux-64.lock

# -----------------
# Primary container
# -----------------
FROM gcr.io/distroless/base-debian10
# copy over the generated environment
COPY --from=builder /opt/env /opt/env
ENV PATH="/opt/env/bin:${PATH}"
```

To get this to work nicely generate the platform specific lock run something
like this in your shell

```bash
# Update the lockfile
conda-lock --kind explicit --platform linux-64
# build the image
docker build -t myimagename:mytag .
```

This will ensure that your conda dependencies used in this docker container are
always exactly reproducible.

## conda-lock inside a build container

You can also use conda-lock with a build-container style system if you make use of
the `--copy` flag from `conda-lock install`

```Dockerfile
# -----------------
# Builder container
# -----------------
FROM condaforge/mambaforge:4.14.0-0 as builder

COPY environment.yml /docker/environment.yml

RUN . /opt/conda/etc/profile.d/conda.sh && \
    mamba create --name lock && \
    conda activate lock && \
    mamba env list && \
    mamba install --yes pip conda-lock>=1.2.2 setuptools wheel && \
    conda-lock lock \
        --platform linux-64 \
        --file /docker/environment.yml \
        --kind lock \
        --lockfile /docker/conda-lock.yml

RUN . /opt/conda/etc/profile.d/conda.sh && \
    conda activate lock && \
    conda-lock install \
        --mamba \
        --copy \
        --prefix /opt/env \
        /docker/conda-lock.yml
# optionally you can perfom some more cleanup on your conda install after this
# to get a leaner conda environment

# -----------------
# Primary container
# -----------------
FROM gcr.io/distroless/base-debian10
COPY --from=builder /opt/env /opt/env
ENV PATH="/opt/env/bin:${PATH}"
```
