"""Smoke tests for the initial Python package scaffold."""

import importlib


def test_package_imports() -> None:
    """Verify editable installation exposes the root package to Python."""
    package = importlib.import_module("atem_ai_vision_mixer")

    assert package.__name__ == "atem_ai_vision_mixer"
