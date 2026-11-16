"""The public code-mode extra and the CI guard that keeps Code Mode tests from skipping."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import code_mode_ci_guard_error

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _project() -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return data


def _fastmcp_floor(specs: list[str]) -> str:
    floors = [match.group(1) for spec in specs if (match := re.fullmatch(r"fastmcp>=(\S+)", spec))]
    assert len(floors) == 1, specs
    return floors[0]


def test_code_mode_extra_is_public_and_pinned_to_the_fastmcp_floor() -> None:
    """``mcp-server-snowflake[code-mode]`` pulls fastmcp[code-mode] at the core floor."""
    project = _project()
    extras = project["optional-dependencies"]
    floor = _fastmcp_floor(project["dependencies"])
    assert extras["code-mode"] == [f"fastmcp[code-mode]>={floor}"]
    assert not any("code-mode" in dep for dep in project["dependencies"])


def test_dev_extra_carries_the_same_code_mode_pin() -> None:
    extras = _project()["optional-dependencies"]
    assert extras["code-mode"][0] in extras["dev"]


def test_ci_guard_ignores_local_runs() -> None:
    assert code_mode_ci_guard_error({}) is None
    assert code_mode_ci_guard_error({"CI": ""}) is None


def test_ci_guard_fails_when_ci_lacks_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs: None)
    error = code_mode_ci_guard_error({"CI": "true"})
    assert error is not None
    assert "--extra code-mode" in error


def test_ci_guard_passes_when_ci_has_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs: object())
    assert code_mode_ci_guard_error({"CI": "true"}) is None
