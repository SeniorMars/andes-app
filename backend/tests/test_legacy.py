from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def test_load_legacy_modules_rejects_unverifiable_pinned_source_dir(
    tmp_path,
    monkeypatch,
):
    source_dir = tmp_path / "source"
    _write_legacy_source(source_dir, "source")
    monkeypatch.setattr("andes_core.legacy._git_revision", lambda _path: None)

    with pytest.raises(RuntimeError, match="no legacy revision"):
        load_legacy_modules(source_dir, expected_revision="abc123")


def test_load_legacy_modules_rejects_source_revision_mismatch(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    _write_legacy_source(source_dir, "source")
    monkeypatch.setattr("andes_core.legacy._git_revision", lambda _path: "actual")

    with pytest.raises(RuntimeError, match="revision mismatch"):
        load_legacy_modules(source_dir, expected_revision="expected")


def test_load_legacy_modules_uses_pinned_adapter_module(tmp_path, monkeypatch):
    adapter_root = tmp_path / "adapter_root"
    adapter = adapter_root / "andes_original_adapter"
    adapter.mkdir(parents=True)
    (adapter / "__init__.py").write_text(
        "\n".join(
            [
                "import types",
                "__andes_revision__ = 'abc123'",
                "load_data = types.ModuleType('adapter_load_data')",
                "load_data.LABEL = 'adapter'",
                "func_optimized = types.ModuleType('adapter_func_optimized')",
                "func_optimized.LABEL = 'adapter'",
                "func_gsea = types.ModuleType('adapter_func_gsea')",
                "func_gsea.LABEL = 'adapter'",
            ]
        ),
        encoding="utf-8",
    )
    source_dir = tmp_path / "unused_source"
    source_dir.mkdir()
    monkeypatch.syspath_prepend(str(adapter_root))

    modules = load_legacy_modules(
        source_dir,
        adapter_module="andes_original_adapter",
        expected_revision="abc123",
    )

    assert modules.source_type == "adapter_module"
    assert modules.adapter_module == "andes_original_adapter"
    assert modules.loaded_revision == "abc123"
    assert modules.load_data.LABEL == "adapter"
    assert str(source_dir) not in sys.path
    assert modules.provenance() == {
        "source_type": "adapter_module",
        "adapter_module": "andes_original_adapter",
        "expected_revision": "abc123",
        "loaded_revision": "abc123",
    }


def test_load_legacy_modules_rejects_adapter_revision_mismatch(tmp_path, monkeypatch):
    adapter_root = tmp_path / "adapter_root"
    adapter = adapter_root / "andes_original_adapter_mismatch"
    adapter.mkdir(parents=True)
    (adapter / "__init__.py").write_text(
        "\n".join(
            [
                "import types",
                "__andes_revision__ = 'actual'",
                "load_data = types.ModuleType('adapter_load_data')",
                "func_optimized = types.ModuleType('adapter_func_optimized')",
                "func_gsea = types.ModuleType('adapter_func_gsea')",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(adapter_root))

    with pytest.raises(RuntimeError, match="revision mismatch"):
        load_legacy_modules(
            tmp_path / "unused_source",
            adapter_module="andes_original_adapter_mismatch",
            expected_revision="expected",
        )
