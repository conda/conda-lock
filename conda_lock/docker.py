import pathlib
import subprocess
import sys
import tempfile
import textwrap
import uuid


template2 = """
FROM {image}
ARG PREFIX

RUN mkdir -p /archive
RUN cd $PREFIX && \
    tar cvf - . | gzip >/archive/out.tar.gz
RUN ls /archive
"""


def make_layered_docker_file_builder(
    lockfile: str,
    prefix: str,
    base_dockerfile: pathlib.Path,
    output_filename: str = "Dockerfile.conda-layered",
):
    """Build a layered dockerfile that contains a given conda environment.

    This will make a new dockerfile that copies the contents from a base dockerfile into itself.
    """
    work_dir = base_dockerfile.parent

    append_to_dockerfile = textwrap.dedent(
        f"""
        ENV _CONDA_LOCK_PREFIX="{prefix}"

        ADD conda_explicit_install.sh /src/conda_explicit_install.sh
        RUN chmod +x /src/conda_explicit_install.sh
        ENV CONDA_ARGS="--copy --quiet --mkdir $CONDA_ARGS"

        ENV CONDA_SAFETY_CHECKS=disabled
        ENV CONDA_ROLLBACK_ENABLED=False

        """
    )

    conda_explicit_install = textwrap.dedent(
        """
        #!/bin/bash
        set -e

        cat << EOF > /dev/shm/explicit-deps
        @EXPLICIT
        $1
        EOF

        conda install -p $_CONDA_LOCK_PREFIX $CONDA_ARGS --file /dev/shm/explicit-deps
        """
    )

    (work_dir / "conda_explicit_install.sh").write_text(conda_explicit_install)
    with (work_dir / output_filename).open("w") as fo:
        fo.writelines([base_dockerfile.read_text(), "\n", append_to_dockerfile])

        with open(lockfile) as locklines:

            for i, line in enumerate(locklines.readlines()):
                line = line.strip()
                if line.startswith("@"):
                    continue
                if line.startswith("#"):
                    continue

                fo.writelines([f"RUN /src/conda_explicit_install.sh {line}\n"])

    return work_dir / output_filename


def build_archive(lockfile, prefix="/opt/env", output_file="out.tar.gz"):
    """Create a reasonably redistributable tarball that contains a given lockfile.

    This will make efficient use of docker layers, adding each dependency as a new layer.

    """

    with tempfile.TemporaryDirectory() as tmpdir:
        p_tmpdir = pathlib.Path(tmpdir)
        base_dockerfile = p_tmpdir / "Dockerfile"
        base_dockerfile.write_text("FROM continuumio/miniconda:latest\n")
        output_dockerfile = make_layered_docker_file_builder(
            lockfile=lockfile, prefix=prefix, base_dockerfile=base_dockerfile
        )

        image_tag = f"temp-{uuid.uuid4().hex}"
        subprocess.check_call(
            ["docker", "build", "--file", output_dockerfile, "--tag", image_tag, "."],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmpdir,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        p_tmpdir = pathlib.Path(tmpdir)
        (p_tmpdir / "Dockerfile").write_text(template2.format(image=image_tag))

        image_tag2 = f"temp-{uuid.uuid4().hex}"
        proc = subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"PREFIX={prefix}",
                "-t",
                image_tag2,
                ".",
            ],
            cwd=tmpdir,
        )
        proc.check_returncode()
        # harvest artifact out of container
        container_id = subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--entrypoint",
                "/bin/bash",
                image_tag2,
                "sleep",
                "infinity",
            ],
            encoding="utf8",
        ).strip()
        try:
            subprocess.check_output(
                ["docker", "cp", f"{container_id}:/archive/out.tar.gz", output_file]
            )
        finally:
            subprocess.check_call(["docker", "stop", container_id])


if __name__ == "__main__":
    build_archive(sys.argv[1])
