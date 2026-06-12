from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class LegacyModules:
    load_data: ModuleType
    func_optimized: ModuleType
    func_gsea: ModuleType


def load_legacy_modules(source_dir: Path) -> LegacyModules:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"ANDES source directory does not exist: {source_dir}")

    source_str = str(source_dir)
    if source_str not in sys.path:
        sys.path.insert(0, source_str)

    return LegacyModules(
        load_data=importlib.import_module("load_data"),
        func_optimized=importlib.import_module("func_optimized"),
        func_gsea=importlib.import_module("func_gsea"),
    )
