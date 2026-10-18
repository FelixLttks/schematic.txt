"""The layout must be identical between runs and between interpreter processes.

Python randomises ``str`` hashing per process, so any iteration over a raw
``set`` of names would make the output depend on PYTHONHASHSEED.  These tests
run the pipeline in fresh interpreters with different seeds.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from schematic import generate_schematic_svg
print(generate_schematic_svg(open({path!r}).read(), dark_theme=False))
"""


def _render(path: Path, seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    out = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(root=str(ROOT), path=str(path))],
        capture_output=True, text=True, env=env, check=True,
    )
    return out.stdout


class TestDeterminism(unittest.TestCase):
    def test_identical_output_across_hash_seeds(self):
        for rel in ("examples/esp32_sensors_bus.txt", "tests/layout_cases/05_i2c_bus.txt",
                    "tests/layout_cases/12_labels_annotations.txt"):
            with self.subTest(case=rel):
                path = ROOT / rel
                first = _render(path, "1")
                self.assertTrue(first.startswith("<svg"))
                self.assertEqual(first, _render(path, "4242"))


if __name__ == "__main__":
    unittest.main()
