"""Where Entune keeps its data."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from entune import paths


def test_entune_data_overrides_the_platform_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("ENTUNE_DATA", raising=False)
    assert paths.default_data_dir() == tmp_path / "Library" / "Application Support" / "entune"
    monkeypatch.setenv("ENTUNE_DATA", str(tmp_path / "chosen"))
    assert paths.default_data_dir() == tmp_path / "chosen"
