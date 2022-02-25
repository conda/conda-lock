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

# Build container
FROM continuumio/miniconda:latest as conda

ADD conda-linux-64.lock /locks/conda-linux-64.lock
RUN conda create -p /opt/env --copy --file /locks/conda-linux-64.lock

# Primary container
FROM gcr.io/distroless/base-debian10
# copy over the generated environment
COPY --from=conda /opt/env /opt/env
```

To get this to work nicely generate the platform specific lock run

```shell
conda lock --format explicit --platform linux-64
docker build .
```

This will ensure that your conda dependencies used in this docker container are
always exactly reproducible.
