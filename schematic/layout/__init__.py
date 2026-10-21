"""Layout engine: netlist -> placement -> routing -> labels -> report.

The hot modules (``estimate``, ``placement``, ``router``) can be compiled
with mypyc (``python3 tools/build_native.py``): same results, about 1.5
times faster.  The compiled build lives in ``_native/`` and is used only
while its stamp matches the current sources and interpreter; otherwise (or
with ``SCHEMATIC_PURE_PYTHON=1``) the plain modules run.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import sysconfig
import warnings
from pathlib import Path

NATIVE_MODULES = ("estimate", "placement", "router")
_HERE = Path(__file__).resolve().parent
_NATIVE_DIR = _HERE / "_native"


def source_digest(name: str) -> str:
    return hashlib.sha256((_HERE / f"{name}.py").read_bytes()).hexdigest()


def _native_ok() -> bool:
    if os.environ.get("SCHEMATIC_PURE_PYTHON"):
        return False
    try:
        stamp = json.loads((_NATIVE_DIR / "stamp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if stamp.get("ext_suffix") != sysconfig.get_config_var("EXT_SUFFIX"):
        return False
    sources = stamp.get("sources", {})
    if any(sources.get(m) != source_digest(m) for m in NATIVE_MODULES):
        warnings.warn("compiled layout modules are out of date, using pure Python "
                      "(rebuild: python3 tools/build_native.py)", RuntimeWarning, stacklevel=2)
        return False
    return True


NATIVE = _native_ok()
if NATIVE:
    # the modules' shared runtime is a top-level module
    sys.path.insert(0, str(_NATIVE_DIR / "lib"))
    __path__.insert(0, str(_NATIVE_DIR))  # type: ignore[name-defined]  # noqa: F821


def reload_pinned() -> set:
    """Modules a hot reload must keep: compiled modules cannot be imported
    afresh (the interpreter hands back their first instance), so they and
    every ``schematic`` module they hold references into stay -- a fresh
    ``Shape`` next to the old one the compiled code still builds breaks
    pickling to worker processes.  Empty for pure Python."""
    if not NATIVE:
        return set()
    import types
    todo = [f"{__name__}.{m}" for m in NATIVE_MODULES]
    pinned = {__name__}
    while todo:
        name = todo.pop()
        mod = sys.modules.get(name)
        if name in pinned or mod is None:
            continue
        pinned.add(name)
        for v in vars(mod).values():
            ref = v.__name__ if isinstance(v, types.ModuleType) else getattr(v, "__module__", None)
            if isinstance(ref, str) and ref.startswith("schematic."):
                todo.append(ref)
    return pinned
