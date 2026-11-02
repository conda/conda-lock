import subprocess

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from conda_lock._vendor.poetry.packages.direct_origin import DirectOrigin
from conda_lock.models.lock_spec import VCSDependency
from conda_lock.pypi_solver import PlatformEnv, solve_pypi


@pytest.mark.parametrize("use_solver", [True, False])
def test_git_source_cache_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_solver: bool
) -> None:
    """A pinned dependency must not reuse a checkout from the old source cache."""

    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Test User",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=tmp_path,
            text=True,
            stderr=subprocess.PIPE,
        ).strip()

    source = tmp_path / "source"
    git("init", "--initial-branch=main", str(source))
    metadata = (
        '[tool.poetry]\nname = "cache-probe"\nversion = "1.0.0"\n'
        'description = "Source cache fixture"\nauthors = []\n'
    )
    (source / "pyproject.toml").write_text(metadata)
    git("-C", str(source), "add", ".")
    git("-C", str(source), "commit", "-m", "Package metadata")
    revision = git("-C", str(source), "rev-parse", "HEAD")

    cache_dir = tmp_path / "cache"
    env_dir = tmp_path / "environment"
    monkeypatch.setenv("POETRY_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("POETRY_SYSTEM_GIT_CLIENT", "false")
    old_checkout = (env_dir if use_solver else cache_dir) / "src" / "cache-probe"
    old_checkout.parent.mkdir(parents=True)
    git("clone", str(source), str(old_checkout))
    # Model a checkout left by the old parser: the SHA matches, but its files do not.
    stale_metadata = metadata.replace('"1.0.0"', '"2.0.0"')
    (old_checkout / "pyproject.toml").write_text(stale_metadata)

    # Keep the resolver offline, including on Windows where file URLs have drives.
    source_url = f"https://example.invalid/{tmp_path.name}/cache-probe.git"
    git_config = tmp_path / "gitconfig"
    git(
        "config",
        "--file",
        str(git_config),
        f"url.{source.as_uri()}.insteadOf",
        source_url,
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}")

    with (
        patch.object(
            PlatformEnv, "path", new_callable=PropertyMock, return_value=env_dir
        ),
        patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Unexpected HTTP request"),
        ),
    ):
        if use_solver:
            dependency = VCSDependency(
                name="cache-probe",
                manager="pip",
                vcs="git",
                source=source_url,
                rev=revision,
            )
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
            version = result[dependency.name].version
            assert result[dependency.name].url == f"git+{source_url}@{revision}"
        else:
            package = DirectOrigin.get_package_from_vcs("git", source_url, rev=revision)
            version = str(package.version)

    assert version == "1.0.0"
    assert (old_checkout / "pyproject.toml").read_text() == stale_metadata
