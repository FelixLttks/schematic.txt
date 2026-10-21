#!/usr/bin/env python3
"""Compiles the layout engine's hot modules with mypyc.

    pip install mypy setuptools        # once, for the interpreter that runs the app
    python3 tools/build_native.py

The same algorithm compiled to C: identical drawings, about 1.5 times
faster.  The build goes to ``schematic/layout/_native/`` with a stamp of the
source hashes; ``schematic.layout`` uses it only while the stamp matches, so
after editing a module the pure Python version runs until you rebuild.
``--clean`` removes the build.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schematic.layout import NATIVE_MODULES, _NATIVE_DIR  # noqa: E402


def main() -> int:
    if "--clean" in sys.argv[1:]:
        shutil.rmtree(_NATIVE_DIR, ignore_errors=True)
        print(f"Removed {_NATIVE_DIR}")
        return 0
    try:
        import mypy.version
        import setuptools  # noqa: F401
    except ImportError:
        print("mypyc missing: pip install mypy setuptools", file=sys.stderr)
        return 1
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    with tempfile.TemporaryDirectory() as tmp_dir:
        # build in a copy, so a failed build never leaves modules next to the sources
        tmp = Path(tmp_dir)
        shutil.copytree(ROOT / "schematic", tmp / "schematic",
                        ignore=shutil.ignore_patterns("__pycache__", "_native", "*.so", "*.pyd"))
        srcs = [f"schematic/layout/{m}.py" for m in NATIVE_MODULES]
        subprocess.run([sys.executable, "-m", "mypyc", *srcs], cwd=tmp, check=True)
        shutil.rmtree(_NATIVE_DIR, ignore_errors=True)
        (_NATIVE_DIR / "lib").mkdir(parents=True)
        for m in NATIVE_MODULES:
            shutil.copy2(tmp / "schematic" / "layout" / f"{m}{suffix}", _NATIVE_DIR)
        for lib in tmp.glob(f"*__mypyc{suffix}"):
            shutil.copy2(lib, _NATIVE_DIR / "lib")
        # hashes of the sources actually compiled
        stamp = {
            "ext_suffix": suffix,
            "python": sys.version,
            "mypy": mypy.version.__version__,
            "sources": {m: hashlib.sha256((tmp / src).read_bytes()).hexdigest()
                        for m, src in zip(NATIVE_MODULES, srcs)},
        }
    (_NATIVE_DIR / "stamp.json").write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    print(f"Compiled {', '.join(NATIVE_MODULES)} -> {_NATIVE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
