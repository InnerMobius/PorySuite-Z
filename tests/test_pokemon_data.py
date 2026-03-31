import os
import json
import tempfile
import unittest
from io import StringIO
import sys
import types

# Ensure the repo root is on sys.path so plugin modules can be imported when
# executed with plain `pytest`
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

qt_module = types.ModuleType("PyQt6")
qt_gui = types.ModuleType("PyQt6.QtGui")
class QImage: pass
class QPixmap: pass
qt_gui.QImage = QImage
qt_gui.QPixmap = QPixmap
qt_module.QtGui = qt_gui
qt_core = types.ModuleType("PyQt6.QtCore")
def pyqtSignal(*args, **kwargs):
    return None
qt_core.pyqtSignal = pyqtSignal
qt_module.QtCore = qt_core
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)
sys.modules.setdefault("PyQt6.QtCore", qt_core)

from plugin_abstract.pokemon_data import AbstractPokemonData

class DummyData(AbstractPokemonData):
    DATA_FILE = "dummy.json"
    def parse_to_c_code(self):
        pass


class BackupData(AbstractPokemonData):
    DATA_FILE = "dummy.json"

    def parse_to_c_code(self):
        super().parse_to_c_code()

class SaveDataTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_info = {"dir": self.tempdir.name, "project_name": "proj"}
        os.makedirs(os.path.join(self.tempdir.name, "src", "data"), exist_ok=True)
        self.data_instance = DummyData(self.project_info)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_save_skips_empty_data(self):
        self.data_instance.data = {}
        self.data_instance.original_data = {"x": 1}
        file_path = os.path.join(self.tempdir.name, "src", "data", DummyData.DATA_FILE)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("oldcontent")
        captured = StringIO()
        stdout = sys.stdout
        sys.stdout = captured
        try:
            self.data_instance.save()
        finally:
            sys.stdout = stdout
        self.assertEqual(captured.getvalue().strip(), "Error: refusing to overwrite with empty data")
        with open(file_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "oldcontent")

    def test_save_writes_when_data_present(self):
        self.data_instance.data = {"a": 1}
        self.data_instance.original_data = {}
        file_path = os.path.join(self.tempdir.name, "src", "data", DummyData.DATA_FILE)
        self.data_instance.save()
        with open(file_path, "r", encoding="utf-8") as f:
            content = json.load(f)
        self.assertEqual(content, {"a": 1})


class ParseMissingFileTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_info = {
            "dir": self.tempdir.name,
            "project_name": "proj",
            "source_prefix": "",
        }
        os.makedirs(os.path.join(self.tempdir.name, "src", "data"), exist_ok=True)
        self.data_instance = BackupData(self.project_info)
        self.data_instance.add_file_to_backup("missing.c", "missing")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_parse_to_c_code_skips_missing_files(self):
        self.data_instance.data = {"a": 1}
        self.data_instance.original_data = {}
        with self.assertLogs(level="WARNING") as cm:
            self.data_instance.parse_to_c_code()
        self.assertTrue(any("missing.c" in msg for msg in cm.output))
        data_file = os.path.join(
            self.tempdir.name, "src", "data", BackupData.DATA_FILE
        )
        self.assertTrue(os.path.isfile(data_file))
        self.assertFalse(
            os.path.exists(os.path.join(self.tempdir.name, "missing.c.bak"))
        )

if __name__ == "__main__":
    unittest.main()
