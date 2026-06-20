from __future__ import annotations

import importlib
import subprocess
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
    source_type: str
    source_dir: Path | None = None
    adapter_module: str | None = None
    expected_revision: str | None = None
    loaded_revision: str | None = None

    def provenance(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_type": self.source_type,
            "expected_revision": self.expected_revision,
            "loaded_revision": self.loaded_revision,
        }
        if self.source_dir is not None:
            payload["source_dir"] = str(self.source_dir)
        if self.adapter_module is not None:
            payload["adapter_module"] = self.adapter_module
        return payload


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


def _git_revision(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path.expanduser().resolve()), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision or None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _assert_revision_matches(
    *,
    expected_revision: str | None,
    loaded_revision: str | None,
    source_label: str,
) -> None:
    if expected_revision is None:
        return
    if loaded_revision is None:
        raise RuntimeError(
            f"ANDES_ORIGINAL_REVISION is set to {expected_revision!r}, but no legacy "
            f"revision could be read from {source_label}"
        )
    if loaded_revision != expected_revision:
        raise RuntimeError(
            f"legacy ANDES revision mismatch for {source_label}: expected "
            f"{expected_revision}, loaded {loaded_revision}"
        )


def _adapter_revision(adapter: ModuleType) -> str | None:
    for attr in ("__andes_revision__", "ANDES_REVISION", "__version__"):
        value = getattr(adapter, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _modules_from_adapter(adapter: ModuleType) -> dict[str, ModuleType]:
    factory = getattr(adapter, "get_legacy_modules", None)
    if callable(factory):
        module_set = factory()
        modules = {name: getattr(module_set, name) for name in LEGACY_MODULE_NAMES}
    else:
        modules = {name: getattr(adapter, name) for name in LEGACY_MODULE_NAMES}
    missing = [name for name, module in modules.items() if not isinstance(module, ModuleType)]
    if missing:
        raise TypeError(
            "legacy adapter module must expose ModuleType attributes for: "
            + ", ".join(missing)
        )
    return modules


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


def _load_adapter_modules(
    *,
    adapter_module: str,
    expected_revision: str | None,
) -> LegacyModules:
    with _legacy_numba_no_cache():
        adapter = importlib.import_module(adapter_module)
        modules = _modules_from_adapter(adapter)
    loaded_revision = _adapter_revision(adapter)
    _assert_revision_matches(
        expected_revision=expected_revision,
        loaded_revision=loaded_revision,
        source_label=f"adapter module {adapter_module!r}",
    )
    return LegacyModules(
        load_data=modules["load_data"],
        func_optimized=modules["func_optimized"],
        func_gsea=modules["func_gsea"],
        source_type="adapter_module",
        adapter_module=adapter_module,
        expected_revision=expected_revision,
        loaded_revision=loaded_revision,
    )


def load_legacy_modules(
    source_dir: Path,
    *,
    adapter_module: str | None = None,
    expected_revision: str | None = None,
) -> LegacyModules:
    adapter_module = _normalize_optional_text(adapter_module)
    expected_revision = _normalize_optional_text(expected_revision)
    if adapter_module is not None:
        return _load_adapter_modules(
            adapter_module=adapter_module,
            expected_revision=expected_revision,
        )

    source_dir = source_dir.expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"ANDES source directory does not exist: {source_dir}")
    loaded_revision = _git_revision(source_dir)
    _assert_revision_matches(
        expected_revision=expected_revision,
        loaded_revision=loaded_revision,
        source_label=str(source_dir),
    )

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
            source_type="source_dir",
            source_dir=source_dir,
            expected_revision=expected_revision,
            loaded_revision=loaded_revision,
        )
