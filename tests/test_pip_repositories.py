import base64
import os
import tarfile

from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import pytest
import requests
import requests_mock

from conda_lock.conda_lock import DEFAULT_LOCKFILE_NAME, run_lock
from conda_lock.lockfile import parse_conda_lock_file
from tests.test_conda_lock import clone_test_dir


_PRIVATE_REPO_USERNAME = "secret-user"
_PRIVATE_REPO_PASSWORD = "secret-password"

_PRIVATE_REPO_ROOT = """<!DOCTYPE html>
<html>
  <body>
    <a href="/api/pypi/simple/fake-private-package/">fake-private-package</a>
  </body>
</html>
"""

_PRIVATE_REPO_PACKAGE = """<!DOCTYPE html>
<html>
  <body>
    <a href="/files/fake-private-package-1.0.0.tar.gz">fake-private-package-1.0.0.tar.gz</a>
  </body>
</html>
"""


@pytest.fixture
def private_package_tar(tmp_path: Path):
    sdist_path = (
        clone_test_dir("test-pip-repositories", tmp_path) / "fake-private-package-1.0.0"
    )
    assert sdist_path.exists()
    tar_path = sdist_path / "fake-private-package-1.0.0.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(sdist_path, arcname=os.path.basename(sdist_path))
    return tar_path


@pytest.fixture(
    autouse=True,
    params=["response_url_without_credentials", "response_url_with_credentials"],
)
def mock_private_pypi(private_package_tar: Path, request: pytest.FixtureRequest):
    with requests_mock.Mocker(real_http=True) as mocker:
        fixture_request = request

        def _make_response(
            request: requests.Request,
            status: int,
            headers: Optional[dict] = None,
            text: str = "",
            reason: str = "",
            file: Optional[str] = None,
        ) -> requests.Response:
            headers = headers or {}
            response = requests.Response()
            response.status_code = status
            for name, value in headers.items():
                response.headers[name] = value
            if not file:
                response.encoding = "utf-8"
                response._content = text.encode(encoding=response.encoding)
                response._content_consumed = True  # type: ignore
            else:
                assert not text
                response.headers.setdefault("Content-Type", "application/octet-stream")
                response.raw = BytesIO()
                with open(file, "rb") as file_handler:
                    response.raw.write(file_handler.read())
                response.raw.seek(0)

            url = urlparse(request.url)
            if fixture_request.param == "response_url_with_credentials":
                response.url = request.url
            else:
                response.url = request.url.replace(url.netloc, url.hostname)
            response.reason = reason
            return response

        def _parse_auth(request: requests.Request) -> Tuple[str, str]:
            url = urlparse(request.url)
            if url.username:
                return url.username, url.password
            header = request.headers.get("Authorization")
            if not header or not header.startswith("Basic"):
                return "", ""
            username, password = (
                base64.b64decode(header.split()[-1]).decode("utf-8").split(":", 1)
            )
            return username, password

        @mocker._adapter.add_matcher
        def handle_request(request: requests.Request) -> Optional[requests.Response]:
            url = urlparse(request.url)
            if url.hostname != "private-pypi.org":
                return None
            username, password = _parse_auth(request)
            if username != _PRIVATE_REPO_USERNAME or password != _PRIVATE_REPO_PASSWORD:
                return _make_response(request, status=401, reason="Not authorized")
            path = url.path.rstrip("/")
            if path == "/api/pypi/simple":
                return _make_response(request, status=200, text=_PRIVATE_REPO_ROOT)
            if path == "/api/pypi/simple/fake-private-package":
                return _make_response(request, status=200, text=_PRIVATE_REPO_PACKAGE)
            if path == "/files/fake-private-package-1.0.0.tar.gz":
                return _make_response(
                    request, status=200, file=str(private_package_tar)
                )
            return _make_response(request, status=404, reason="Not Found")

        yield


@pytest.fixture(autouse=True)
def configure_auth(monkeypatch):
    monkeypatch.setenv("PIP_USER", _PRIVATE_REPO_USERNAME)
    monkeypatch.setenv("PIP_PASSWORD", _PRIVATE_REPO_PASSWORD)


def test_it_uses_pip_repositories_with_env_var_substitution(
    monkeypatch: "pytest.MonkeyPatch",
    conda_exe: str,
    tmp_path: Path,
):
    # GIVEN an environment.yaml with custom pip repositories
    directory = clone_test_dir("test-pip-repositories", tmp_path)
    monkeypatch.chdir(directory)
    environment_file = directory / "environment.yaml"
    assert environment_file.exists(), list(directory.iterdir())

    # WHEN I create the lockfile
    run_lock([directory / "environment.yaml"], conda_exe=conda_exe)

    # THEN the lockfile is generated correctly
    lockfile_path = directory / DEFAULT_LOCKFILE_NAME
    assert lockfile_path.exists(), list(directory.iterdir())
    lockfile = parse_conda_lock_file(lockfile_path)
    lockfile_content = lockfile_path.read_text(encoding="utf-8")
    packages = {package.name: package for package in lockfile.package}

    # AND the private package is in the lockfile
    package = packages.get("fake-private-package")
    assert package, lockfile_content

    package_url = urlparse(package.url)

    # AND the package was sourced from the private repository
    assert package_url.hostname == "private-pypi.org", (
        "Package was fetched from incorrect host. See full lock-file:\n"
        + lockfile_content
    )

    # AND environment variables are occluded
    assert package_url.username == "$PIP_USER", (
        "User environment variable was not respected, See full lock-file:\n"
        + lockfile_content
    )
    assert package_url.password == "$PIP_PASSWORD", (
        "Password environment variable was not respected, See full lock-file:\n"
        + lockfile_content
    )
