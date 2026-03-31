import os
import sys
import tempfile
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")

def pyqtSignal(*args, **kwargs):
    return None

qt_core.pyqtSignal = pyqtSignal
qt_module.QtCore = qt_core
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)

from local_env import LocalUtil


class RepoRootFallbackTest(unittest.TestCase):
    def test_repo_root_climbs_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.join(tmpdir, "proj")
            nested = os.path.join(base, "a", "b")
            os.makedirs(nested)
            os.makedirs(os.path.join(base, "src"))
            os.makedirs(os.path.join(base, "include"))
            with open(os.path.join(base, "project.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            util = LocalUtil({"dir": nested, "project_name": "proj"})
            self.assertEqual(util.repo_root(), base)

    def test_repo_root_uses_config_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.join(tmpdir, "proj")
            nested = os.path.join(base, "x", "y")
            os.makedirs(nested)
            os.makedirs(os.path.join(base, "src"))
            os.makedirs(os.path.join(base, "include"))
            with open(os.path.join(base, "config.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            util = LocalUtil({"dir": nested, "project_name": "proj"})
            self.assertEqual(util.repo_root(), base)

    def test_repo_root_returns_base_when_no_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "a", "b")
            os.makedirs(nested)
            util = LocalUtil({"dir": nested, "project_name": "proj"})
            expected = nested
            self.assertEqual(util.repo_root(), expected)

    def test_repo_root_skips_invalid_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.join(tmpdir, "proj")
            os.makedirs(os.path.join(base, "src"))
            os.makedirs(os.path.join(base, "include"))
            parent = os.path.dirname(base)
            with open(os.path.join(parent, "project.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            util = LocalUtil({"dir": base, "project_name": "proj"})
            self.assertEqual(util.repo_root(), base)


if __name__ == "__main__":
    unittest.main()
