"""Shared fixtures and import wiring for the Toolbox Bridge test suite.

bridge.py lives at the repo root (it is a single-file script, not a package),
so we add the repo root to sys.path here and expose it as the `bridge` module
to every test file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import bridge  # noqa: E402  (path must be set up first)


@pytest.fixture
def bridge_module():
    """The bridge.py module under test."""
    return bridge


def entry(
    path: str,
    *,
    risk: str | None = None,
    owner: str | None = None,
    description: str | None = None,
) -> dict:
    """Build a Bulwark-shaped scan entry, omitting unset fields."""
    e: dict = {"path": path}
    if risk is not None:
        e["risk"] = risk
    if owner is not None:
        e["owner"] = owner
    if description is not None:
        e["description"] = description
    return e


@pytest.fixture
def make_entry():
    """Factory fixture so tests can build entries without importing the helper."""
    return entry


@pytest.fixture
def script(tmp_path):
    """Create a fake script file under tmp_path and return its path string.

    The bridge never reads the script body; it only needs a path to derive the
    sidecar location from, but creating a real file keeps the scenarios honest.
    """
    def _make(name: str = "do-thing.sh", body: str = "#!/bin/sh\necho hi\n") -> str:
        p = tmp_path / name
        p.write_text(body)
        return str(p)

    return _make
