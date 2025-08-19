import base64
import os
import re
import tarfile

from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pytest
import requests
import requests_mock

from conda_lock.conda_lock import DEFAULT_LOCKFILE_NAME, run_lock
from conda_lock.lockfile import parse_conda_lock_file
from conda_lock.lookup import DEFAULT_MAPPING_URL
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
    """A private package to be served from the default port"""
    sdist_path = (
        clone_test_dir("test-pip-repositories", tmp_path) / "fake-private-package-1.0.0"
    )
    assert sdist_path.exists()
    tar_path = sdist_path / "fake-private-package-1.0.0.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(sdist_path, arcname=os.path.basename(sdist_path))
    return tar_path


@pytest.fixture
def private_package_tar_custom_port(tmp_path: Path):
    """A second private package to be served from a non-default port"""
    sdist_path = (
        clone_test_dir("test-pip-repositories", tmp_path)
        / "fake-private-package-custom-port-1.0.0"
    )
    assert sdist_path.exists()
    tar_path = sdist_path / "fake-private-package-custom-port-1.0.0.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(sdist_path, arcname=os.path.basename(sdist_path))
    return tar_path


@pytest.fixture(
    autouse=True,
    params=["response_url_without_credentials", "response_url_with_credentials"],
)
def mock_private_pypi(  # noqa: C901
    private_package_tar: Path,
    private_package_tar_custom_port: Path,
    request: pytest.FixtureRequest,
):
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

            if fixture_request.param == "response_url_with_credentials":
                response.url = request.url
            else:
                # Strip credentials using regex, preserving port if present:
                # ^([^:]+://) - Capture group 1: scheme (http:// or https://)
                # [^@]+@     - Match and remove credentials (anything up to @)
                # \1         - Replace with just the captured scheme
                response.url = re.sub(r"^([^:]+://)[^@]+@", r"\1", request.url)
            response.reason = reason
            return response

        def _parse_auth(request: requests.Request) -> tuple[str, str]:
            url = urlparse(request.url)
            if url.username:
                assert url.password is not None
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
            """Intercept requests to private-pypi.org and private-pypi-custom-port.org.

            Requests to other hosts are passed through to the real internet.

            On private-pypi.org:80, we publish fake-private-package.

            On private-pypi-custom-port.org:8080, we publish fake-private-package-custom-port.
            """
            url = urlparse(request.url)
            if url.hostname not in ["private-pypi.org", "private-pypi-custom-port.org"]:
                # Bail out and use normal requests.get()
                return None
            username, password = _parse_auth(request)
            if username != _PRIVATE_REPO_USERNAME or password != _PRIVATE_REPO_PASSWORD:
                return _make_response(request, status=401, reason="Not authorized")
            path = url.path.rstrip("/")
            if url.port:
                port = url.port
            elif url.scheme == "https":
                port = 443
            elif url.scheme == "http":
                port = 80
            else:
                raise ValueError(f"Unknown scheme: {url.scheme}")
            if url.hostname == "private-pypi.org":
                if port != 80:
                    return None
                text = ""
                file = None
                if path == "/api/pypi/simple":
                    text = _PRIVATE_REPO_ROOT
                if path == "/api/pypi/simple/fake-private-package":
                    text = _PRIVATE_REPO_PACKAGE
                if path == "/files/fake-private-package-1.0.0.tar.gz":
                    file = str(private_package_tar)
                if text == "" and file is None:
                    return _make_response(request, status=404, reason="Not Found")
                return _make_response(request, status=200, text=text, file=file)
            elif url.hostname == "private-pypi-custom-port.org":
                if port != 8080:
                    return None
                text = ""
                file = None
                if path == "/api/pypi/simple":
                    text = _PRIVATE_REPO_ROOT.replace(
                        "fake-private-package", "fake-private-package-custom-port"
                    )
                if path == "/api/pypi/simple/fake-private-package-custom-port":
                    text = _PRIVATE_REPO_PACKAGE.replace(
                        "fake-private-package", "fake-private-package-custom-port"
                    )
                if path == "/files/fake-private-package-custom-port-1.0.0.tar.gz":
                    file = str(private_package_tar_custom_port)
                if text == "" and file is None:
                    return _make_response(request, status=404, reason="Not Found")
                return _make_response(request, status=200, text=text, file=file)
            else:
                return None

        yield


@pytest.fixture(autouse=True)
def configure_auth(monkeypatch):
    monkeypatch.setenv("PIP_USER", _PRIVATE_REPO_USERNAME)
    monkeypatch.setenv("PIP_PASSWORD", _PRIVATE_REPO_PASSWORD)


def test_it_uses_pip_repositories_with_env_var_substitution(
    monkeypatch: "pytest.MonkeyPatch",
    conda_exe: str,
    tmp_path: Path,
    cleared_poetry_cache: None,
):
    # GIVEN an environment.yaml with custom pip repositories and clean cache
    directory = clone_test_dir("test-pip-repositories", tmp_path)
    monkeypatch.chdir(directory)
    environment_file = directory / "environment.yaml"
    assert environment_file.exists(), list(directory.iterdir())

    # WHEN I create the lockfile
    run_lock(
        [directory / "environment.yaml"],
        conda_exe=conda_exe,
        mapping_url=DEFAULT_MAPPING_URL,
    )

    # THEN the lockfile is generated correctly
    lockfile_path = directory / DEFAULT_LOCKFILE_NAME
    assert lockfile_path.exists(), list(directory.iterdir())
    lockfile = parse_conda_lock_file(lockfile_path)
    lockfile_content = lockfile_path.read_text(encoding="utf-8")
    packages = {package.name: package for package in lockfile.package}

    # AND the private packages are in the lockfile
    for package_name in ["fake-private-package", "fake-private-package-custom-port"]:
        package = packages.get(package_name)
        assert package, lockfile_content

        package_url = urlparse(package.url)

        # AND the package was sourced from the private repository
        expected_hostname = (
            "private-pypi.org"
            if package_name == "fake-private-package"
            else "private-pypi-custom-port.org"
        )
        assert package_url.hostname == expected_hostname, (
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
