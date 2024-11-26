FROM continuumio/miniconda3:latest

RUN pip install conda-lock

ENTRYPOINT conda-lock
