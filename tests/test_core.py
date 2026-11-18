#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心逻辑测试：不联网、不依赖真实模型。

    python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".test-tmp")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
os.environ["MEMORY_DIR"] = os.path.join(TMP, "memories")
os.environ.pop("MEMORY_LLM_API_KEY", None)
os.environ["MEMORY_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"  # 故意的死端口：测试不该联网
os.environ["MEMORY_LLM_MODEL"] = "none"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness  # noqa: E402

harness.JOURNAL_DIR = os.path.join(TMP, "journal")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def reset():
    """清空记忆目录，给每个用例一个干净环境。"""
    if os.path.isdir(harness.MEM_DIR):
        shutil.rmtree(harness.MEM_DIR)
    if os.path.isdir(harness.JOURNAL_DIR):
        shutil.rmtree(harness.JOURNAL_DIR)
    os.makedirs(harness.MEM_DIR, exist_ok=True)


class TestStore(unittest.TestCase):
    def setUp(self):
        reset()

    def test_save_writes_frontmatter_and_atomic(self):
        name, path = harness.save_memory("深色主题", "用户偏好深色主题", "user", "喜欢深色界面。")
        text = harness._read_text_file(path)
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: 深色主题", text)
        self.assertEqual(os.listdir(harness.MEM_DIR).count("MEMORY.md"), 1)
        leftovers = [f for f in os.listdir(harness.MEM_DIR) if ".tmp" in f]
        self.assertEqual(leftovers, [], "原子写不应留下临时文件")

    def test_description_newline_is_flattened(self):
        _, path = harness.save_memory("多行描述", "第一行\n第二行", "user", "内容")
        meta, _ = harness.parse_memory(harness._read_text_file(path))
        self.assertNotIn("\n", meta["description"])
        self.assertEqual(meta["description"], "第一行 第二行")

    def test_find_memory_by_frontmatter_name(self):
        """导入的 md 若 frontmatter name 与文件名不一致，也要能定位（修旧版 bug）。"""
        path = os.path.join(harness.MEM_DIR, "some-slug.md")
        harness._atomic_write(path, "---\nname: 别人的名字\ntype: user\n---\n\n正文\n")
        harness.rebuild_index()
        found = harness.find_memory("别人的名字")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "some-slug")

    def test_soft_delete_goes_to_trash(self):
        harness.save_memory("待删条目", "试删", "reference", "内容")
        target = harness.delete_memory("待删条目")
        self.assertTrue(os.path.isfile(target))
        self.assertIsNone(harness.find_memory("待删条目"))
        self.assertIn(harness.TRASH_DIR, target)

    def test_hard_delete(self):
        harness.save_memory("彻底删", "试删", "reference", "内容")
        path = harness.delete_memory("彻底删", purge=True)
        self.assertFalse(os.path.exists(path))


class TestIndexAndRetrieve(unittest.TestCase):
    def setUp(self):
        reset()
        harness.save_memory("深色主题偏好", "用户喜欢深色主题的界面", "user", "她要求所有工具都开深色模式。")
        harness.save_memory("python习惯", "用户写 Python 偏简单脚本", "feedback",
                            "不用框架，标准库优先。")
        harness.save_memory("项目约定", "记忆存本地 md", "project", "不要用数据库，别存云端。")

    def test_tokenize_chinese_bigram(self):
        self.assertEqual(harness.tokenize("深色"), {"深色"})
        self.assertTrue({"主题", "偏好"} <= harness.tokenize("深色主题偏好"))

    def test_search_word_order_does_not_matter(self):
        """旧版是子串匹配，换词序就搜不到；现在按 bigram 命中率。"""
        ranked = harness.rank_entries("主题偏好")
        self.assertTrue(ranked, "换个词序也应该命中")
        self.assertEqual(ranked[0][1]["name"], "深色主题偏好")

    def test_retrieve_digest_tier(self):
        r = harness.retrieve("深色主题")
        self.assertEqual(r["tier"], "digest")
        self.assertGreaterEqual(r["count"], 1)
        self.assertIn("深色主题偏好", r["names"])

    def test_retrieve_recent_tier_is_limited(self):
        r = harness.retrieve("量子纠缠与超导材料")
        self.assertEqual(r["tier"], "recent")
        self.assertLessEqual(r["count"], 8)
        self.assertIn("最近", r["text"])
        self.assertNotIn("# 记忆库", r["text"])

    def test_retrieve_respects_budget(self):
        r = harness.retrieve("记忆", budget_chars=120)
        self.assertLessEqual(len(r["text"]), 400)

    def test_index_rebuild_on_change(self):
        idx = harness.load_index()
        self.assertEqual(len(idx["entries"]), 3)
        harness.save_memory("新条目", "新增", "project", "x")
        self.assertEqual(len(harness.load_index()["entries"]), 4)


class TestChatCompaction(unittest.TestCase):
    def setUp(self):
        reset()
        self._orig_summarize = harness.summarize_text
        harness.summarize_text = lambda text, max_chars=400: "（栈替换摘要）"

    def tearDown(self):
        harness.summarize_text = self._orig_summarize

    def test_under_budget_keeps_all(self):
        s = harness.ChatSession(budget=10000)
        s.messages = [{"role": "user", "content": "短"}, {"role": "assistant", "content": "短答"}]
        self.assertFalse(s.compact_if_needed())
        self.assertEqual(len(s.messages), 2)

    def test_over_budget_summarizes_and_keeps_recent(self):
        s = harness.ChatSession(budget=50, keep_recent=2)
        s.messages = [{"role": "user", "content": "第%d轮 " % i + "内容" * 20} for i in range(6)]
        self.assertTrue(s.compact_if_needed())
        self.assertEqual(len(s.messages), 2, "只留最近两轮")
        self.assertIn("[c1]", s.summary)
        self.assertTrue(os.path.isfile(os.path.join(harness.JOURNAL_DIR, "c1.md")),
                        "被压缩的原文必须落盘")
        self.assertEqual(s.blocks, ["c1"])

    def test_second_compaction_increments_block(self):
        s = harness.ChatSession(budget=50, keep_recent=2)
        for _ in range(2):
            s.messages += [{"role": "user", "content": "x" * 60},
                           {"role": "assistant", "content": "y" * 60},
                           {"role": "user", "content": "z" * 60},
                           {"role": "assistant", "content": "w" * 60}]
            s.compact_if_needed()
        self.assertEqual(s.blocks, ["c1", "c2"])
        self.assertIn("[c2]", s.summary)

    def test_state_roundtrip(self):
        s = harness.ChatSession.from_state([{"role": "user", "content": "嗨"}], "旧摘要")
        st = s.state()
        s2 = harness.ChatSession.from_state(st["history"], st["summary"])
        self.assertEqual(s2.summary, "旧摘要")
        self.assertEqual(len(s2.messages), 1)

    def test_blocks_survive_roundtrip_and_never_overwrite(self):
        """网页端每次请求都是从 state 复原的新会话，块号必须接着数，不能重号覆盖。"""
        s = harness.ChatSession(budget=50, keep_recent=2)
        s.messages = [{"role": "user", "content": "第%d轮 " % i + "内容" * 20} for i in range(6)]
        s.compact_if_needed()
        st = s.state()

        s2 = harness.ChatSession.from_state(st["history"], st["summary"], st["blocks"])
        s2.budget, s2.keep_recent = 50, 2          # from_state 不带预算，测试里手动设小
        s2.messages += [{"role": "user", "content": "x" * 60},
                        {"role": "assistant", "content": "y" * 60},
                        {"role": "user", "content": "z" * 60},
                        {"role": "assistant", "content": "w" * 60}]
        s2.compact_if_needed()

        self.assertEqual(s2.blocks, ["c1", "c2"])
        c1 = open(os.path.join(harness.JOURNAL_DIR, "c1.md"), encoding="utf-8").read()
        self.assertNotIn("x" * 60, c1, "第二轮不能覆盖第一轮的原文")

    def test_block_numbering_continues_after_restart(self):
        """命令行重开一个会话（不带 blocks）也要接着 journal/ 里已有的块号。"""
        s = harness.ChatSession(budget=50, keep_recent=2)
        s.messages = [{"role": "user", "content": "第%d轮 " % i + "内容" * 20} for i in range(6)]
        s.compact_if_needed()
        s3 = harness.ChatSession(budget=50, keep_recent=2)
        self.assertEqual(s3.blocks, ["c1"])


PNG_1PX = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
           "QVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class TestImages(unittest.TestCase):
    def setUp(self):
        reset()

    def test_persist_image_writes_file_and_returns_path(self):
        rel = harness.persist_image(PNG_1PX)
        self.assertIn("journal/images/", rel)
        self.assertTrue(os.path.isfile(os.path.join(harness.ROOT, rel)), rel)
        self.assertNotIn("base64", rel)

    def test_bad_image_rejected(self):
        with self.assertRaises(ValueError):
            harness.img_decode("data:image/bmp;base64,AAAA")
        with self.assertRaises(ValueError):
            harness.img_decode("http://example.com/a.png")

    def test_image_counts_toward_budget(self):
        s = harness.ChatSession(budget=100, keep_recent=1)
        s.messages = [{"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": PNG_1PX}}]}]
        self.assertGreaterEqual(s.size(), harness.IMG_WEIGHT)

    def test_compaction_writes_path_not_base64(self):
        """压进 journal 的是图片路径标记，绝不能是几十兆的 base64。"""
        rel = harness.persist_image(PNG_1PX)
        s = harness.ChatSession(budget=100, keep_recent=1)
        s.messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "这张图"},
                {"type": "text", "text": f"[图片 {rel}]"},
                {"type": "image_url", "image_url": {"url": PNG_1PX}}]},
            {"role": "assistant", "content": "看到了"},
            {"role": "user", "content": "再说一次 " + "补充" * 40},
            {"role": "assistant", "content": "好"}]
        self.assertTrue(s.compact_if_needed())
        c1 = open(os.path.join(harness.JOURNAL_DIR, "c1.md"), encoding="utf-8").read()
        self.assertIn("这张图", c1)
        self.assertIn(rel, c1)
        self.assertNotIn("base64", c1)

    def test_state_strips_images(self):
        """网页端 state 只回传文字，否则每轮请求都带几兆 base64。"""
        s = harness.ChatSession()
        s.messages = [{"role": "user", "content": [
            {"type": "text", "text": "图"},
            {"type": "image_url", "image_url": {"url": PNG_1PX}}]}]
        st = s.state()
        self.assertEqual(st["history"][0]["content"], "图")

    def test_ask_sends_multimodal_and_persists(self):
        seen = {}

        def fake_llm(msgs, **kw):
            seen["msgs"] = msgs
            return "ok"

        orig = harness.llm
        harness.llm = fake_llm
        try:
            s = harness.ChatSession()
            ans, _ = s.ask("这是什么", [PNG_1PX])
        finally:
            harness.llm = orig
        self.assertEqual(ans, "ok")
        user = seen["msgs"][-1]["content"]
        self.assertIsInstance(user, list)
        self.assertIn("image_url", [p["type"] for p in user])
        self.assertTrue(any("journal/images/" in p.get("text", "") for p in user))
        self.assertEqual(len(os.listdir(harness.img_dir())), 1)


class TestWebApi(unittest.TestCase):
    """网页端 handler 的往返：图片校验、剥图、压缩续号（不真的起服务器）。"""

    def setUp(self):
        reset()
        import webui
        self.webui = webui
        self._llm = harness.llm
        self._budget = harness.CFG.get("history_budget")
        harness.llm = lambda msgs, **kw: "好的"

    def tearDown(self):
        harness.llm = self._llm
        harness.CFG["history_budget"] = self._budget

    def _hist(self, n, size=20):
        return [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": "第%d轮 " % i + "内容" * size} for i in range(n)]

    def test_rejects_non_image(self):
        out = self.webui.handle_api("/api/chat", {"message": "hi",
                                                  "images": ["data:text/plain;base64,AAA"]})
        self.assertIn("error", out)

    def test_rejects_too_many_images(self):
        out = self.webui.handle_api("/api/chat", {"message": "hi",
                                                  "images": [PNG_1PX] * 5})
        self.assertIn("error", out)

    def test_chat_with_image_compacts_and_keeps_block_numbers(self):
        harness.CFG["history_budget"] = 300
        out = self.webui.handle_api("/api/chat", {
            "message": "这张图什么颜色", "history": self._hist(10), "summary": "",
            "blocks": [], "images": [PNG_1PX]})
        self.assertNotIn("error", out, out)
        self.assertEqual(out["blocks"], ["c1"])
        self.assertEqual(out["images"], 1)
        for m in out["history"]:                     # 回传的 state 必须已经把图剥掉
            self.assertIsInstance(m["content"], str)
            self.assertNotIn("base64", m["content"])
        self.assertIn("journal/images/", out["history"][-2]["content"],
                      "图片要落成路径标记，而不是留在上下文里当 base64")
        for f in os.listdir(harness.JOURNAL_DIR):
            p = os.path.join(harness.JOURNAL_DIR, f)
            if os.path.isfile(p):
                self.assertNotIn("base64", open(p, encoding="utf-8").read(), f)

        out2 = self.webui.handle_api("/api/chat", {
            "message": "再说一遍", "history": self._hist(10) + out["history"],
            "summary": out["summary"], "blocks": out["blocks"], "images": [PNG_1PX]})
        self.assertEqual(out2["blocks"], ["c1", "c2"], "第二块不能覆盖 c1")


class TestLan(unittest.TestCase):
    """局域网模式：只对外报真正能用的网段，口令生成的地址要能拼出来。"""

    def test_private_filter(self):
        import webui
        for ip in ("192.168.1.5", "10.0.0.2", "172.16.0.9", "172.31.255.1"):
            self.assertTrue(webui._private(ip), ip)
        for ip in ("127.0.0.1", "169.254.123.68", "172.32.0.1", "8.8.8.8", "192.169.1.1"):
            self.assertFalse(webui._private(ip), ip)

    def test_lan_ips_returns_only_private(self):
        import webui
        self.assertTrue(all(webui._private(ip) for ip in webui.lan_ips()))


class TestModelSwitch(unittest.TestCase):
    """模型列表与切换：未知名字要拒绝，切换后要写进 config.json。"""

    def setUp(self):
        self.old_cfg = harness.CONFIG_FILE
        self.old_models = list(harness.MODELS)
        self.old_model = harness.MODEL
        self.cfg_path = os.path.join(TMP, "switch-config.json")
        if os.path.exists(self.cfg_path):
            os.remove(self.cfg_path)
        harness.CONFIG_FILE = self.cfg_path
        harness.MODELS = harness._norm_models(
            [{"id": "glm-4v-flash", "label": "看图版"}, "glm-4-flash"], "glm-4v-flash")

    def tearDown(self):
        harness.CONFIG_FILE = self.old_cfg
        harness.MODELS = self.old_models
        harness.MODEL = self.old_model
        if os.path.exists(self.cfg_path):
            os.remove(self.cfg_path)

    def test_current_model_is_always_in_list(self):
        models = harness._norm_models(["only-one"], "another")
        self.assertEqual(models[0]["id"], "another")
        ids = [m["id"] for m in models]
        self.assertIn("only-one", ids)

    def test_str_items_get_a_label(self):
        models = harness._norm_models(["abc"], "abc")
        self.assertEqual(models, [{"id": "abc", "label": "abc"}])

    def test_switch_persists_to_config(self):
        harness.set_model("glm-4-flash")
        self.assertEqual(harness.MODEL, "glm-4-flash")
        with open(self.cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["model"], "glm-4-flash")

    def test_unknown_model_rejected(self):
        with self.assertRaises(ValueError):
            harness.set_model("not-a-model")
        with self.assertRaises(ValueError):
            harness.set_model("")
        self.assertEqual(harness.MODEL, self.old_model)


if __name__ == "__main__":
    unittest.main(verbosity=2)
