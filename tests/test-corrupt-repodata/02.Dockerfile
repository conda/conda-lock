ARG MICROMAMBA_TAG=latest

FROM mambaorg/micromamba:${MICROMAMBA_TAG}

COPY 01-explicit.lock /tmp/explicit.lock

RUN micromamba install -y -p /opt/conda -f /tmp/explicit.lock

