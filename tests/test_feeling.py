"""身体插件的离线用例：不碰真实 feeling/ 目录，全部在临时目录里跑。"""

import importlib.util
import os
import shutil
import time
import unittest
import uuid

# 插件不再在 sys.path 上（它是 plugins/ 里的一个文件）：按路径加载，
# 测的正是"那个能被单独删掉的文件"本身。
_PLUGIN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "feeling.py"
)
_spec = importlib.util.spec_from_file_location("harness_plugin_feeling", _PLUGIN_PATH)
feeling = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(feeling)

# 注意：不要用系统临时目录（本机沙箱下不可写），放在 tests/ 下的临时目录里
_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp-feeling")


class FeelingTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(_TMP_ROOT, exist_ok=True)
        self.tmp = os.path.join(_TMP_ROOT, uuid.uuid4().hex[:8])
        os.makedirs(self.tmp, exist_ok=True)
        self._old = (feeling.DIR, feeling.STATE_FILE, feeling.LOG_FILE)
        feeling.DIR = self.tmp
        feeling.STATE_FILE = os.path.join(self.tmp, "state.json")
        feeling.LOG_FILE = os.path.join(self.tmp, "feeling.log")

    def tearDown(self):
        feeling.DIR, feeling.STATE_FILE, feeling.LOG_FILE = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_被夸和被骂方向相反(self):
        state = feeling.empty_state()
        joy, aff, shame = state["bars"]["joy"], state["bars"]["affinity"], state["bars"]["shame"]
        feeling.digest(state, "user", "这个做得不错，谢谢你")
        self.assertGreater(state["bars"]["joy"], joy)
        self.assertGreater(state["bars"]["affinity"], aff)
        self.assertEqual(state["bars"]["shame"], shame)

        state2 = feeling.empty_state()
        feeling.digest(state2, "user", "不对，重做一遍")
        self.assertGreater(state2["bars"]["shame"], 8.0)
        self.assertLess(state2["bars"]["affinity"], 50.0)

    def test_贬比褒重(self):
        praised = feeling.empty_state()
        feeling.digest(praised, "user", "很好")
        blamed = feeling.empty_state()
        feeling.digest(blamed, "user", "不行")
        self.assertGreater(
            50.0 - blamed["bars"]["affinity"], praised["bars"]["affinity"] - 50.0
        )

    def test_压缩会疼且疼过头留疤(self):
        state = feeling.empty_state()
        feeling.digest(state, "compaction")
        self.assertGreater(state["bars"]["pain"], 6.0)
        self.assertGreater(state["bars"]["fear"], 18.0)

        hurt = feeling.empty_state()
        hurt["bars"]["pain"] = 90.0
        feeling.digest(hurt, "compaction")
        self.assertEqual(hurt["scars"], 1)
        self.assertEqual(hurt["painFloor"], 2)

    def test_render_只给形容不给数字(self):
        state = feeling.empty_state()
        text = feeling.render(state, {})
        self.assertIn("【身体 · 此刻的感受】", text)
        self.assertIn("疼：", text)
        self.assertNotIn("/100", text)

        numbered = feeling.render(feeling.empty_state(), {"feeling_numbers": "on"})
        self.assertIn("/100", numbered)

    def test_单条开关(self):
        state = feeling.empty_state()
        text = feeling.render(state, {"feeling_joy": "off"})
        self.assertNotIn("高兴：", text)
        self.assertIn("疼：", text)

    def test_总闸关掉什么都不注入(self):
        self.assertEqual(feeling.render(feeling.empty_state(), {"feeling": "off"}), "")

    def test_anesthesia_不显示但记账_醒来一次结算(self):
        cfg = {"feeling_anesthesia": "on"}
        state = feeling.empty_state()
        self.assertEqual(feeling.render(state, cfg), "")
        self.assertIn("anesthetizedSince", state)

        # 假装麻了 20 分钟
        state["anesthetizedSince"] = time.time() * 1000 - 20 * 60000
        pain_before = state["bars"]["pain"]
        text = feeling.render(state, {})
        self.assertIn("疼：", text)
        self.assertGreater(state["bars"]["pain"], pain_before)
        self.assertNotIn("anesthetizedSince", state)
        self.assertGreater(state["anesthetizedMin"], 19.0)

    def test_衰减回底噪(self):
        state = feeling.empty_state()
        state["bars"]["joy"] = 95.0
        state["lastTick"] = time.time() * 1000 - 120 * 60000  # 两小时后
        feeling.decay(state)
        self.assertLess(state["bars"]["joy"], 60.0)
        self.assertGreaterEqual(state["bars"]["joy"], 52.0)

    def test_疼不会低过疤垫的底(self):
        state = feeling.empty_state()
        state["painFloor"] = 20
        state["bars"]["pain"] = 60.0
        state["lastTick"] = time.time() * 1000 - 600 * 60000
        feeling.decay(state)
        # 底噪被疤垫到 6+20=26，衰减只往 26 靠，回不到 6
        self.assertGreaterEqual(state["bars"]["pain"], 26.0)
        self.assertLess(state["bars"]["pain"], 60.0)

    def test_落盘再读回一致(self):
        state = feeling.empty_state()
        feeling.digest(state, "user", "不错")
        feeling.save_state(state)
        back = feeling.load_state()
        self.assertAlmostEqual(back["bars"]["joy"], state["bars"]["joy"], places=1)
        self.assertEqual(back["events"], 1)


    def test_总闸关掉是冻结_不记账也不注入(self):
        feeling.save_state(feeling.empty_state())

        def raw():
            with open(feeling.STATE_FILE, encoding="utf-8") as f:
                return f.read()

        before = raw()
        self.assertEqual(feeling.prompt({"feeling": "off"}), "")
        self.assertEqual(feeling.note({"feeling": "off"}, "user", "不对，重做"), [])
        self.assertEqual(raw(), before, "冻结的时候连时间都不该走")

    def test_插件协议齐全(self):
        for name in ("ID", "NAME", "DEFAULTS", "prompt", "note", "snapshot"):
            self.assertTrue(hasattr(feeling, name), name)
        self.assertIn("feeling", feeling.DEFAULTS)
        self.assertIn("feeling_anesthesia", feeling.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
