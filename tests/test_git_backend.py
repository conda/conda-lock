import subprocess

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from conda_lock._vendor.poetry.vcs.git.backend import Git
from conda_lock.models.lock_spec import VCSDependency
from conda_lock.pypi_solver import PlatformEnv, solve_pypi


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=path,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--initial-branch=main")
    (source / "content.txt").write_text("first")
    # Dulwich 1.2.9 could not read loose objects larger than 8192 bytes.
    (source / "large.txt").write_bytes(b"x" * 10000)
    git(source, "add", ".")
    git(source, "commit", "-m", "First fixture commit")
    git(source, "tag", "-a", "v1", "-m", "Annotated tag")
    (source / "content.txt").write_text("second")
    git(source, "commit", "-am", "Second fixture commit")
    return source


@pytest.mark.parametrize("ref", ["branch", "tag", "revision", "short_revision"])
def test_dulwich_clone_refs(source: Path, tmp_path: Path, ref: str) -> None:
    first = git(source, "rev-parse", "v1^{commit}")
    expected = git(source, "rev-parse", "main") if ref == "branch" else first
    revision = first if ref == "revision" else None
    if ref == "short_revision":
        revision = first[:8]

    with (
        patch.object(Git, "is_using_legacy_client", return_value=False),
        patch.object(
            Git, "_clone_legacy", side_effect=AssertionError("Unexpected fallback")
        ),
    ):
        for _ in range(2):
            # Exercise both the initial clone and an existing checkout.
            with Git.clone(
                source.as_uri(),
                source_root=tmp_path / "clones",
                branch="main" if ref == "branch" else None,
                tag="v1" if ref == "tag" else None,
                revision=revision,
            ) as repo:
                assert Git.get_revision(repo) == expected
                assert (Path(repo.path) / "content.txt").read_text() == (
                    "second" if ref == "branch" else "first"
                )
                assert (Path(repo.path) / "large.txt").read_bytes() == b"x" * 10000


def test_dulwich_clone_submodule(source: Path, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init", "--initial-branch=main")
    git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        source.as_uri(),
        "child",
    )
    git(parent, "commit", "-am", "Fixture with submodule")
    with (
        patch.object(Git, "is_using_legacy_client", return_value=False),
        patch.object(
            Git, "_clone_legacy", side_effect=AssertionError("Unexpected fallback")
        ),
        Git.clone(parent.as_uri(), source_root=tmp_path / "clones") as repo,
    ):
        assert (Path(repo.path) / "child" / "content.txt").read_text() == "second"


def test_lock_git_package(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (source / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "fixture-vcs"\nversion = "1.2.3"\n'
        'description = "Git resolver fixture"\nauthors = []\n'
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "Add package metadata")
    git(source, "tag", "package-v1")
    revision = git(source, "rev-parse", "HEAD")
    # Keep the clone local without exposing Windows drive letters to Poetry's
    # file-URL parser. Dulwich applies Git's insteadOf rule before cloning.
    source_url = "https://example.invalid/fixture-vcs.git"
    git_config = tmp_path / "gitconfig"
    git(
        tmp_path,
        "config",
        "--file",
        str(git_config),
        f"url.{source.as_uri()}.insteadOf",
        source_url,
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    dependency = VCSDependency(
        name="fixture-vcs",
        manager="pip",
        vcs="git",
        source=source_url,
        rev="package-v1",
    )
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}")

    with (
        patch.object(
            PlatformEnv, "path", new_callable=PropertyMock, return_value=tmp_path
        ),
        patch.object(Git, "is_using_legacy_client", return_value=False),
        patch.object(
            Git, "_clone_legacy", side_effect=AssertionError("Unexpected fallback")
        ),
        patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Unexpected HTTP request"),
        ),
    ):
        result = solve_pypi(
            pip_specs={dependency.name: dependency},
            use_latest=[],
            pip_locked={},
            conda_locked={},
            python_version="3.10.20",
            platform="linux-64",
            allow_pypi_requests=False,
            mapping_url=str(mapping),
        )

    package = result["fixture-vcs"]
    assert package.version == "1.2.3"
    assert package.url == f"git+{source_url}@{revision}"
    assert package.hash.sha256 == revision
