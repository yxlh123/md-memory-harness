"""插件管理的离线用例：装 / 停用 / 装回去 / 卸载 / 坏文件，全在临时目录里跑。"""

import io
import os
import shutil
import unittest
import uuid
import zipfile

import harness

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp-plugins")

GOOD = '''
ID = "dummy"
NAME = "测试插件"
DEFAULTS = {"dummy_on": "on"}

def prompt(cfg):
    return "dummy"

def note(cfg, kind, text=""):
    return []

def snapshot(cfg):
    return {"ok": True}
'''


class PluginManageTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(_ROOT, exist_ok=True)
        self.tmp = os.path.join(_ROOT, uuid.uuid4().hex[:8])
        os.makedirs(self.tmp)
        self._old = (harness.PLUGIN_DIR, dict(harness.PLUGINS),
                     dict(harness._DEFAULTS), dict(harness.CFG))
        harness.PLUGIN_DIR = self.tmp
        harness.PLUGINS.clear()
        harness._PLUGIN_ERRORS.clear()

    def tearDown(self):
        harness.PLUGIN_DIR, plugins, defaults, cfg = self._old
        harness.PLUGINS.clear()
        harness.PLUGINS.update(plugins)
        harness._DEFAULTS.clear()
        harness._DEFAULTS.update(defaults)
        harness.CFG.clear()
        harness.CFG.update(cfg)
        harness._PLUGIN_ERRORS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _meta(self):
        return {row["id"]: row for row in harness.plugin_meta()["items"]}

    def test_install_py_and_merge_defaults(self):
        harness.install_plugin_file("dummy.py", GOOD.encode())
        self.assertIn("dummy", harness.PLUGINS)
        self.assertEqual(harness._DEFAULTS["dummy_on"], "on")
        self.assertEqual(harness.PLUGINS["dummy"].NAME, "测试插件")
        self.assertIn("dummy", self._meta())

    def test_install_rejects_bad_syntax_and_bad_name(self):
        with self.assertRaises(ValueError):
            harness.install_plugin_file("broken.py", b"def (:\n")
        with self.assertRaises(ValueError):
            harness.install_plugin_file("../evil.py", GOOD.encode())
        self.assertFalse(os.listdir(self.tmp))

    def test_toggle_off_and_back(self):
        harness.install_plugin_file("dummy.py", GOOD.encode())
        harness.toggle_plugin("dummy", False)
        self.assertNotIn("dummy", harness.PLUGINS)
        self.assertTrue(self._meta()["dummy"]["disabled"])
        harness.toggle_plugin("dummy", True)
        self.assertIn("dummy", harness.PLUGINS)
        self.assertFalse(self._meta()["dummy"].get("disabled", False))

    def test_uninstall_moves_files_to_trash(self):
        harness.install_plugin_file("dummy.py", GOOD.encode())
        harness.install_plugin_file("dummy.ui.html", b"<div>hi</div>")
        harness.uninstall_plugin("dummy")
        self.assertNotIn("dummy", harness.PLUGINS)
        self.assertEqual(harness.plugin_meta()["items"], [])
        trash = os.listdir(os.path.join(self.tmp, "_trash"))
        self.assertTrue(any(name.endswith("_dummy.py") for name in trash))
        self.assertTrue(any(name.endswith("_dummy.ui.html") for name in trash))

    def test_install_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("dummy.py", GOOD)
            z.writestr("dummy.ui.html", "<div>hi</div>")
        harness.install_plugin_zip(buf.getvalue())
        self.assertIn("dummy", harness.PLUGINS)
        self.assertTrue(self._meta()["dummy"]["ui"])

    def test_zip_without_py_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "nothing here")
        with self.assertRaises(ValueError):
            harness.install_plugin_zip(buf.getvalue())

    def test_broken_file_is_listed_not_swallowed(self):
        with open(os.path.join(self.tmp, "oops.py"), "w", encoding="utf-8") as f:
            f.write("def (:\n")
        harness.reload_plugins()
        row = self._meta()["oops"]
        self.assertTrue(row["broken"])
        self.assertTrue(harness.plugin_meta()["errors"])


if __name__ == "__main__":
    unittest.main()
