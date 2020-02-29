FROM continuumio/miniconda:latest

RUN pip install conda-lock

ENTRYPOINT conda-lock