from __future__ import annotations

import sys
from pathlib import Path

from andes_core.legacy import load_legacy_modules


def _write_legacy_source(path: Path, label: str) -> None:
    path.mkdir()
    (path / "load_data.py").write_text(f"LABEL = {label!r}\n", encoding="utf-8")
    (path / "func_optimized.py").write_text(f"LABEL = {label!r}\n", encoding="utf-8")
    (path / "func_gsea.py").write_text(f"LABEL = {label!r}\n", encoding="utf-8")


def test_load_legacy_modules_reloads_when_source_dir_changes(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_legacy_source(first, "first")
    _write_legacy_source(second, "second")

    first_modules = load_legacy_modules(first)
    second_modules = load_legacy_modules(second)

    assert first_modules.load_data.LABEL == "first"
    assert second_modules.load_data.LABEL == "second"
    assert Path(second_modules.func_optimized.__file__).parent == second
    assert Path(sys.modules["func_gsea"].__file__).parent == second
