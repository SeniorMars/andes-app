from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import ModuleType

LEGACY_MODULE_NAMES = ("load_data", "func_optimized", "func_gsea")
_IMPORT_LOCK = RLock()


@dataclass(frozen=True)
class LegacyModules:
    load_data: ModuleType
    func_optimized: ModuleType
    func_gsea: ModuleType


def _module_source_dir(module: ModuleType) -> Path | None:
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        return None
    return Path(module_file).expanduser().resolve().parent


def _clear_legacy_modules() -> None:
    for name in LEGACY_MODULE_NAMES:
        sys.modules.pop(name, None)


def _import_legacy_module_set() -> dict[str, ModuleType]:
    return {
        name: importlib.import_module(name)
        for name in LEGACY_MODULE_NAMES
    }


@contextmanager
def _legacy_numba_no_cache():
    import numba

    original_jit = numba.jit

    def jit_without_disk_cache(*args, **kwargs):
        kwargs["cache"] = False
        return original_jit(*args, **kwargs)

    numba.jit = jit_without_disk_cache
    try:
        yield
    finally:
        numba.jit = original_jit


def load_legacy_modules(source_dir: Path) -> LegacyModules:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"ANDES source directory does not exist: {source_dir}")

    with _IMPORT_LOCK:
        if any(
            name in sys.modules and _module_source_dir(sys.modules[name]) != source_dir
            for name in LEGACY_MODULE_NAMES
        ):
            _clear_legacy_modules()

        source_str = str(source_dir)
        if source_str in sys.path:
            sys.path.remove(source_str)
        sys.path.insert(0, source_str)
        importlib.invalidate_caches()

        with _legacy_numba_no_cache():
            modules = _import_legacy_module_set()
        for name, module in modules.items():
            if _module_source_dir(module) != source_dir:
                raise ImportError(
                    f"loaded legacy module {name!r} from {getattr(module, '__file__', None)!r}; "
                    f"expected a file under {source_dir}"
                )

        return LegacyModules(
            load_data=modules["load_data"],
            func_optimized=modules["func_optimized"],
            func_gsea=modules["func_gsea"],
        )
