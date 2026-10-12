"""Unit tests for the CLI-probe helpers in ``conda_lock.invoke_conda``."""

from __future__ import annotations

import pathlib

from typing import Literal

import pytest

from conda_lock.invoke_conda import get_pkgs_dirs


@pytest.mark.parametrize(
    ("method", "payload", "expected"),
    [
        # micromamba: `config --json list pkgs_dirs`
        ("config", '{"pkgs_dirs": ["/a/pkgs", "/b/pkgs"]}', ["/a/pkgs", "/b/pkgs"]),
        # conda/mamba: `info --json` uses the "package cache" key
        ("info", '{"package cache": ["/c/pkgs"]}', ["/c/pkgs"]),
    ],
)
def test_get_pkgs_dirs_parses_both_json_shapes(
    monkeypatch, method: Literal["config", "info"], payload: str, expected: list[str]
):
    monkeypatch.setattr(
        "conda_lock.invoke_conda.subprocess.check_output",
        lambda args, env: payload.encode(),
    )
    result = get_pkgs_dirs(conda="/dummy/solver", platform="linux-64", method=method)
    assert result == [pathlib.Path(p) for p in expected]


def test_get_pkgs_dirs_rejects_unknown_json_shape(monkeypatch):
    monkeypatch.setattr(
        "conda_lock.invoke_conda.subprocess.check_output",
        lambda args, env: b'{"something else": []}',
    )
    with pytest.raises(ValueError, match="Unable to extract pkgs_dirs"):
        get_pkgs_dirs(conda="/dummy/solver", platform="linux-64", method="config")


def test_get_pkgs_dirs_defaults_method_by_binary(monkeypatch):
    """micromamba probes via `config`, anything else via `info`."""
    seen_args = []

    def fake_check_output(args, env):
        seen_args.append(args)
        return b'{"pkgs_dirs": []}' if "config" in args else b'{"package cache": []}'

    monkeypatch.setattr(
        "conda_lock.invoke_conda.subprocess.check_output", fake_check_output
    )
    get_pkgs_dirs(conda="/opt/micromamba", platform="linux-64")
    get_pkgs_dirs(conda="/opt/conda", platform="linux-64")
    assert "config" in seen_args[0]
    assert "info" in seen_args[1]
