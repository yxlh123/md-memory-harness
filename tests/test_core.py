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
# 测试默认关掉语义检索：真实的 config.json 里可能开着，那会让用例依赖本机 embedding 服务
harness.EMBED_MODEL = ""
harness.EMBED_BASE_URL = ""


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def _force_rmtree(path):
    """Windows 上 git 留下的文件可能带只读位，rmtree 会半途而废——再试一次。"""
    shutil.rmtree(path, ignore_errors=True)
    if not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path):
        for fn in files:
            try:
                os.chmod(os.path.join(root, fn), 0o600)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


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

    def test_summary_index_is_written_to_disk(self):
        """压完必须有一份摘要落在 journal/.summary.md，不能只活在内存/浏览器里。"""
        s = harness.ChatSession(budget=50, keep_recent=2)
        s.messages = [{"role": "user", "content": "第%d轮 " % i + "内容" * 20} for i in range(6)]
        s.compact_if_needed()
        path = os.path.join(harness.JOURNAL_DIR, ".summary.md")
        self.assertTrue(os.path.isfile(path), "摘要索引没落盘")
        text = open(path, encoding="utf-8").read()
        self.assertIn("[c1]", text)
        self.assertEqual(harness.load_summary(), text)

    def test_summary_comes_back_after_page_reload(self):
        """浏览器刷新后 history 是空的：摘要和块号要从磁盘捡回来。"""
        s = harness.ChatSession(budget=50, keep_recent=2)
        s.messages = [{"role": "user", "content": "第%d轮 " % i + "内容" * 20} for i in range(6)]
        s.compact_if_needed()

        fresh = harness.ChatSession.from_state([], "", [])      # 等价于刚打开的网页
        self.assertIn("[c1]", fresh.summary)
        self.assertEqual(fresh.blocks, ["c1"])

    def test_summary_file_not_listed_as_block(self):
        s = harness.ChatSession(budget=50, keep_recent=2)
        s.messages = [{"role": "user", "content": "第%d轮 " % i + "内容" * 20} for i in range(6)]
        s.compact_if_needed()
        self.assertEqual(harness.list_journal(), ["c1"], ".summary.md 不能被当成对话块")


PNG_1PX = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
           "QVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class TestImages(unittest.TestCase):
    def setUp(self):
        reset()
        self._llm = harness.llm
        harness.llm = lambda msgs, **kw: "- [c1] 图片对话摘要"   # 压缩时别真去联网

    def tearDown(self):
        harness.llm = self._llm

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

    def test_switch_carries_provider_base_url(self):
        """每条模型自带 base_url：切模型时地址要跟着换，不能还打上一家的接口。"""
        old_url = harness.BASE_URL
        try:
            harness.MODELS = [
                {"id": "a", "label": "a", "base_url": "http://a.example/v1"},
                {"id": "b", "label": "b", "base_url": "http://b.example/v1"},
            ]
            harness.MODEL = "a"
            harness.BASE_URL = "http://a.example/v1"
            harness.set_model("b")
            self.assertEqual(harness.MODEL, "b")
            self.assertEqual(harness.BASE_URL, "http://b.example/v1")
            harness.set_model("a")
            self.assertEqual(harness.BASE_URL, "http://a.example/v1")
        finally:
            harness.BASE_URL = old_url

    def test_legacy_list_inherits_global_base_url(self):
        """老配置（裸 id、地址全局）洗出来每条都要有 base_url，否则切了地址不跟。"""
        models = harness._norm_models(["x", "y"], "x", "http://z.example/v1")
        self.assertTrue(all(m.get("base_url") == "http://z.example/v1" for m in models))

    def test_cannot_remove_model_in_use(self):
        with self.assertRaises(ValueError):
            harness.remove_model(harness.MODEL)
        harness.add_model("spare-model", "备用", "http://spare.example/v1")
        harness.remove_model("spare-model")
        self.assertNotIn("spare-model", [m["id"] for m in harness.MODELS])


class TestAutoCapture(unittest.TestCase):
    """自动捕获记忆：auto 直接存、suggest 只给候选、off 不打扰。"""

    def setUp(self):
        reset()
        self.old_llm = harness.llm
        self.old_mode = harness.CFG.get("auto_save")

    def tearDown(self):
        harness.llm = self.old_llm
        harness.CFG["auto_save"] = self.old_mode

    def _fake(self, items):
        def llm(messages, temperature=0.3):
            text = messages[-1]["content"]
            if "记忆提取器" in messages[0]["content"]:
                return json.dumps({"items": items}, ensure_ascii=False)
            return "好的。" if "记住" not in text else "记住了。"
        return llm

    def test_auto_mode_saves_directly(self):
        harness.CFG["auto_save"] = "auto"
        harness.llm = self._fake([{"name": "回复偏好", "description": "喜欢结论先说",
                                   "type": "user", "body": "用户要求结论先说，别啰嗦。"}])
        s = harness.ChatSession(budget=10000)
        answer, r = s.ask("我不喜欢啰嗦的回复，结论先说")
        self.assertEqual(r["saved"], ["回复偏好"])
        self.assertEqual(r["suggested"], [])
        names = [n for n, _, _, _ in harness.read_all()]
        self.assertIn("回复偏好", names, "auto 模式必须真落盘")

    def test_suggest_mode_does_not_write(self):
        harness.CFG["auto_save"] = "suggest"
        harness.llm = self._fake([{"name": "回复偏好", "description": "喜欢结论先说",
                                   "type": "user", "body": "用户要求结论先说。"}])
        s = harness.ChatSession(budget=10000)
        answer, r = s.ask("我不喜欢啰嗦的回复")
        self.assertEqual(r["saved"], [])
        self.assertEqual([c["name"] for c in r["suggested"]], ["回复偏好"])
        self.assertEqual([n for n, _, _, _ in harness.read_all()], [], "suggest 不该写盘")

    def test_off_mode_skips_extraction(self):
        harness.CFG["auto_save"] = "off"
        called = []

        def llm(messages, temperature=0.3):
            called.append(messages[0]["content"][:10])
            return "好的。"
        harness.llm = llm
        s = harness.ChatSession(budget=10000)
        s.ask("我不喜欢啰嗦的回复")
        self.assertEqual(len(called), 1, "off 模式不该再花一次提取调用")

    def test_duplicate_is_not_saved_twice(self):
        harness.CFG["auto_save"] = "auto"
        harness.llm = self._fake([{"name": "回复偏好", "description": "结论先说",
                                   "type": "user", "body": "用户要求结论先说。"}])
        s = harness.ChatSession(budget=10000)
        s.ask("我不喜欢啰嗦的回复")
        s.ask("再说一次，我不喜欢啰嗦的回复")
        names = [n for n, _, _, _ in harness.read_all()]
        self.assertEqual(len(names), 1, "同一条不该存两遍")


def write_mem(fn, name, desc, mtype, created, body):
    """直接落一条记忆，方便测排序（created 可控）。"""
    path = os.path.join(harness.MEM_DIR, fn)
    harness._atomic_write(path, (f"---\nname: {name}\ndescription: {desc}\n"
                                 f"type: {mtype}\ncreated: {created}\n---\n\n{body}\n"))
    return path


class TestRanking(unittest.TestCase):
    """v2.6.0 检索加权：IDF / 类型 / 新近。"""

    def setUp(self):
        reset()

    def test_idf_prefers_rare_token(self):
        """「用户」到处都有，「qwen2.5」只有一条有，后者该排前面。"""
        today = harness.datetime.date.today().isoformat()
        write_mem("a.md", "用户偏好", "用户界面偏好", "user", today, "用户喜欢深色界面")
        write_mem("c.md", "用户习惯", "用户的习惯", "user", today, "用户习惯说中文")
        write_mem("b.md", "qwen 版本", "本机模型", "reference", today, "本机跑 qwen2.5:7b")
        harness.build_index()
        ranked = harness.rank_entries("用户 qwen2.5")
        names = [e["name"] for _, e in ranked]
        self.assertEqual(names[0], "qwen 版本", f"稀有词应排第一，实际 {names}")
        score = {e["name"]: s for s, e in ranked}
        self.assertGreater(score["qwen 版本"], score["用户偏好"])

    def test_oov_tokens_do_not_dilute(self):
        """问句里带生词（打错字）不该把分数摊薄到 0.5 以下。"""
        today = harness.datetime.date.today().isoformat()
        write_mem("b.md", "qwen 版本", "本机模型", "reference", today, "本机跑 qwen2.5:7b")
        harness.build_index()
        ranked = harness.rank_entries("qwen2.5 生僻词汇abc")
        self.assertTrue(ranked)
        self.assertGreaterEqual(ranked[0][0], 0.9, "生词应被忽略，不该稀释分母")

    def test_recency_boost_breaks_ties(self):
        old = (harness.datetime.date.today() - harness.datetime.timedelta(days=400)).isoformat()
        today = harness.datetime.date.today().isoformat()
        write_mem("old.md", "老条目", "同一件事", "user", old, "同一个内容")
        write_mem("new.md", "新条目", "同一件事", "user", today, "同一个内容")
        harness.build_index()
        ranked = harness.rank_entries("同一个内容")
        self.assertEqual([e["name"] for _, e in ranked][0], "新条目")

    def test_type_boost_prefers_user(self):
        today = harness.datetime.date.today().isoformat()
        write_mem("r.md", "参考资料", "同一件事", "reference", today, "同样的正文内容")
        write_mem("u.md", "本人偏好", "同一件事", "user", today, "同样的正文内容")
        harness.build_index()
        ranked = harness.rank_entries("同样的正文内容")
        self.assertEqual([e["name"] for _, e in ranked][0], "本人偏好")

    def test_old_index_schema_rebuilds(self):
        write_mem("a.md", "某条", "说明", "user", "2026-01-01", "正文")
        harness._atomic_write(harness.IDX_FILE, json.dumps(
            {"version": 2, "signature": [], "entries": []}, ensure_ascii=False))
        idx = harness.load_index()
        self.assertEqual(idx["version"], harness.INDEX_SCHEMA)
        self.assertIn("df", idx)
        self.assertEqual(idx["n"], 1)

    def test_df_counts_documents_not_occurrences(self):
        today = harness.datetime.date.today().isoformat()
        write_mem("a.md", "甲", "说明", "user", today, "用户 用户 用户 都出现")
        write_mem("b.md", "乙", "说明", "user", today, "别的词")
        harness.build_index()
        idx = harness.load_index()
        self.assertEqual(idx["df"]["用户"], 1, "同一条里出现三次也只算一篇文档")


class _FakeSSEServer:
    """本机起一个假的 /chat/completions：按要求回 SSE 或整段 JSON。"""

    def __init__(self, mode="sse", pieces=("你", "好", "呀")):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        self.pieces = list(pieces)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                if mode == "sse":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.end_headers()
                    for p in outer.pieces:
                        line = json.dumps({"choices": [{"delta": {"content": p}}]}, ensure_ascii=False)
                        self.wfile.write(b"data: " + line.encode("utf-8") + b"\n\n")
                    self.wfile.write(b"data: [DONE]\n\n")
                else:                       # 忽略 stream=true 的服务端
                    body = json.dumps({"choices": [{"message": {"content": "".join(outer.pieces)}}]},
                                      ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


class TestStreaming(unittest.TestCase):
    def setUp(self):
        reset()
        self._old_url = harness.BASE_URL
        self._old_stream = harness.llm_stream
        self._old_key = harness.API_KEY
        harness.API_KEY = ""

    def tearDown(self):
        harness.BASE_URL = self._old_url
        harness.llm_stream = self._old_stream
        harness.API_KEY = self._old_key

    def test_llm_stream_parses_sse(self):
        with _FakeSSEServer("sse", ("你", "好", "呀")) as srv:
            harness.BASE_URL = srv.url
            got = list(harness.llm_stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(got, ["你", "好", "呀"])

    def test_llm_stream_falls_back_when_server_ignores_stream(self):
        with _FakeSSEServer("json", ("整段", "返回")) as srv:
            harness.BASE_URL = srv.url
            got = list(harness.llm_stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(got, ["整段返回"])

    def test_ask_stream_finishes_and_appends_history(self):
        harness.save_memory("偏好", "喜欢短句", "user", "回答尽量短。")

        def fake_stream(msgs, temperature=0.3):
            for p in ("先给", "结论。"):
                yield p

        harness.llm_stream = fake_stream
        harness.CFG["auto_save"] = "off"
        sess = harness.ChatSession(budget=10000)
        pieces = list(sess.ask_stream("怎么答"))
        self.assertEqual("".join(pieces), "先给结论。")
        self.assertEqual([m["role"] for m in sess.messages], ["user", "assistant"])
        self.assertEqual(sess.messages[-1]["content"], "先给结论。")
        answer, r = sess.last_result
        self.assertEqual(answer, "先给结论。")
        self.assertIn("tier", r)
        harness.CFG["auto_save"] = "auto"

    def test_stream_triggers_compaction_like_ask(self):
        def fake_stream(msgs, temperature=0.3):
            yield "好"

        harness.llm = lambda msgs, **kw: "- [c1] 旧内容"   # 压缩摘要用
        harness.llm_stream = fake_stream
        harness.CFG["auto_save"] = "off"
        sess = harness.ChatSession(budget=20, keep_recent=2)
        for q in ("第一句话比较长一点点", "第二句话也比较长一点点", "第三句还要长一点点哦"):
            list(sess.ask_stream(q))
        self.assertTrue(sess.blocks, "超预算后应该压缩出块")
        self.assertTrue(os.path.exists(harness.summary_path()))
        harness.CFG["auto_save"] = "auto"

    def test_web_chat_stream_endpoint(self):
        """真起一个 webui 服务，验证 SSE 回包结构。"""
        import threading
        import urllib.request
        import webui

        def fake_stream(msgs, temperature=0.3):
            for p in ("流", "式", "好"):
                yield p

        harness.llm_stream = fake_stream
        harness.CFG["auto_save"] = "off"
        srv = webui.ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{srv.server_address[1]}/api/chat_stream",
                data=json.dumps({"message": "在吗", "history": [], "summary": "", "blocks": []}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertIn("event-stream", r.headers.get("Content-Type", ""))
                raw = r.read().decode("utf-8")
        finally:
            srv.shutdown()
            srv.server_close()
            harness.CFG["auto_save"] = "auto"
        events = [json.loads(l[5:].strip()) for l in raw.splitlines() if l.startswith("data:")]
        deltas = "".join(e["delta"] for e in events if "delta" in e)
        self.assertEqual(deltas, "流式好")
        done = events[-1]
        self.assertTrue(done.get("done"))
        self.assertEqual(done["reply"], "流式好")
        self.assertEqual(done["history"][-1]["content"], "流式好")


class TestMeta(unittest.TestCase):
    """#5 元数据：updated / source / 命中统计。"""

    def setUp(self):
        reset()

    def test_save_writes_updated(self):
        _, path = harness.save_memory("深色主题", "用户偏好深色主题", "user", "喜欢深色界面。")
        meta, _ = harness.parse_memory(harness._read_text_file(path))
        self.assertEqual(meta["updated"], harness.datetime.date.today().isoformat())
        self.assertEqual(meta["created"], meta["updated"])

    def test_source_is_recorded_and_optional(self):
        _, path = harness.save_memory("自动捕获", "模型自己记的", "user", "内容", source="chat 自动捕获")
        meta, _ = harness.parse_memory(harness._read_text_file(path))
        self.assertEqual(meta["source"], "chat 自动捕获")
        _, path2 = harness.save_memory("手动", "没写来源", "user", "内容")
        meta2, _ = harness.parse_memory(harness._read_text_file(path2))
        self.assertNotIn("source", meta2)

    def test_touch_usage_accumulates(self):
        harness.save_memory("深色主题", "用户偏好深色主题", "user", "喜欢深色界面。")
        harness.touch_usage(["深色主题"])
        harness.touch_usage(["深色主题"])
        u = harness.load_usage()["深色主题"]
        self.assertEqual(u["uses"], 2)
        self.assertEqual(u["last"], harness.datetime.date.today().isoformat())

    def test_retrieve_records_usage(self):
        harness.save_memory("深色主题", "用户偏好深色主题", "user", "界面用深色，别太白。")
        r = harness.retrieve("深色主题")
        self.assertIn("深色主题", r["names"])
        self.assertEqual(harness.load_usage()["深色主题"]["uses"], 1)

    def test_usage_file_does_not_invalidate_index(self):
        harness.save_memory("深色主题", "用户偏好深色主题", "user", "喜欢深色界面。")
        harness.load_index()
        before = os.stat(harness.IDX_FILE).st_mtime_ns
        harness.touch_usage(["深色主题"])           # 只写 .usage.json，不该动到 md 的 mtime
        harness.load_index()
        self.assertEqual(os.stat(harness.IDX_FILE).st_mtime_ns, before, "写统计不该触发索引重建")


class TestSupersede(unittest.TestCase):
    """#2 记忆版本：新版存进来，旧版降级但文件保留。"""

    def setUp(self):
        reset()

    def test_new_version_records_supersedes(self):
        harness.save_memory("深色主题", "用户喜欢深色", "user", "界面要深色。")
        name, path = harness.save_memory("浅色主题", "用户改喜欢浅色了", "user", "界面要浅色。",
                                         supersedes="深色主题")
        meta, _ = harness.parse_memory(harness._read_text_file(path))
        self.assertEqual(meta["supersedes"], "深色主题")
        self.assertEqual(harness.find_memory("深色主题")[1]["status"], "superseded")
        self.assertEqual(harness.find_memory("深色主题")[1]["superseded_by"], name)
        self.assertTrue(os.path.isfile(harness.find_memory("深色主题")[3]), "旧文件不该被删")

    def test_superseded_entry_is_not_retrieved(self):
        harness.save_memory("深色主题", "用户喜欢深色", "user", "界面要深色，别太白。")
        harness.save_memory("浅色主题", "用户改喜欢浅色了", "user", "界面要浅色，护眼。",
                            supersedes="深色主题")
        names = harness.retrieve("深色主题")["names"]
        self.assertNotIn("深色主题", names)
        self.assertIn("浅色主题", names)

    def test_restore_makes_it_active_again(self):
        harness.save_memory("深色主题", "用户喜欢深色", "user", "界面要深色。")
        harness.save_memory("浅色主题", "用户改喜欢浅色了", "user", "界面要浅色。",
                            supersedes="深色主题")
        harness.restore_memory("深色主题")
        meta = harness.find_memory("深色主题")[1]
        self.assertNotEqual(harness.status_of(meta), "superseded")
        self.assertIn("深色主题", harness.retrieve("深色主题")["names"])

    def test_index_schema_carries_status(self):
        harness.save_memory("深色主题", "用户喜欢深色", "user", "界面要深色。")
        harness.save_memory("浅色主题", "用户改喜欢浅色了", "user", "界面要浅色。",
                            supersedes="深色主题")
        idx = harness.load_index()
        self.assertEqual(idx["version"], harness.INDEX_SCHEMA)
        st = {e["name"]: e["status"] for e in idx["entries"]}
        self.assertEqual(st["深色主题"], "superseded")
        self.assertEqual(st["浅色主题"], "active")

    def test_supersede_missing_old_does_not_lose_new(self):
        name, _ = harness.save_memory("浅色主题", "用户改喜欢浅色了", "user", "界面要浅色。",
                                      supersedes="并不存在")
        self.assertTrue(os.path.isfile(harness.memory_path(name)), "旧名打错也不该把这次保存弄失败")

    def test_web_supersede_and_restore(self):
        import webui
        harness.save_memory("深色主题", "用户喜欢深色", "user", "界面要深色。")
        self._llm = harness.llm
        harness.llm = lambda msgs, **kw: "浅色主题"
        try:
            out = webui.handle_api("/api/supersede", {"old": "深色主题",
                                                      "text": "界面还是浅色护眼",
                                                      "type": "user", "use_llm": False})
        finally:
            harness.llm = self._llm
        self.assertNotIn("error", out, out)
        self.assertEqual(out["superseded"], "深色主题")
        st = {m["name"]: m["status"] for m in webui.api_state()["memories"]}
        self.assertEqual(st["深色主题"], "superseded")
        back = webui.handle_api("/api/restore", {"name": "深色主题"})
        self.assertTrue(back.get("ok"), back)
        st2 = {m["name"]: m["status"] for m in webui.api_state()["memories"]}
        self.assertEqual(st2["深色主题"], "active")


class TestUsageStats(unittest.TestCase):
    """#9 token 用量：归一化各家字段 → 累计到 .stats.json → 每轮能报出来。"""

    def setUp(self):
        reset()
        self._llm = harness.llm
        self._auto = harness.CFG.get("auto_save")
        harness.CFG["auto_save"] = "off"      # 只看回答那一次调用，别被自动记记忆的调用干扰

    def tearDown(self):
        harness.llm = self._llm
        harness.LAST_USAGE.clear()
        if self._auto is None:
            harness.CFG.pop("auto_save", None)
        else:
            harness.CFG["auto_save"] = self._auto

    def test_norm_usage_accepts_both_naming_styles(self):
        self.assertEqual(harness._norm_usage({"prompt_tokens": 10, "completion_tokens": 5}),
                         {"prompt": 10, "completion": 5, "total": 15})
        self.assertEqual(harness._norm_usage({"input_tokens": 7, "output_tokens": 3}),
                         {"prompt": 7, "completion": 3, "total": 10})
        self.assertEqual(harness._norm_usage({"prompt_tokens": 4, "total_tokens": 9})["total"], 9)
        self.assertEqual(harness._norm_usage({"foo": 1}), {})
        self.assertEqual(harness._norm_usage(None), {})

    def test_record_usage_accumulates_total_and_daily(self):
        harness.record_usage({"prompt_tokens": 100, "completion_tokens": 20})
        harness.record_usage({"prompt_tokens": 50, "completion_tokens": 10})
        stats = harness.load_stats()
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["prompt_tokens"], 150)
        self.assertEqual(stats["completion_tokens"], 30)
        day = list(stats["days"].values())[0]
        self.assertEqual((day["calls"], day["prompt"], day["completion"]), (2, 150, 30))
        self.assertEqual(harness.LAST_USAGE, {"prompt": 50, "completion": 10, "total": 60})

    def test_stats_file_is_not_a_memory(self):
        harness.record_usage({"prompt_tokens": 1, "completion_tokens": 1})
        self.assertTrue(os.path.exists(harness.stats_path()))
        self.assertEqual(harness.read_all(), [], ".stats.json 不该被当成一条记忆")
        harness.rebuild_index()
        self.assertEqual(harness.load_index()["entries"], [])

    def test_ask_exposes_this_turn_usage(self):
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色。")

        def fake(messages, **kw):
            harness.record_usage({"prompt_tokens": 123, "completion_tokens": 45})
            return "好的，记住了。"

        harness.llm = fake
        sess = harness.ChatSession()
        sess.budget, sess.keep_recent = 100000, 4
        answer, r = sess.ask("界面用什么颜色")
        self.assertEqual(answer, "好的，记住了。")
        self.assertEqual(r["usage"], {"calls": 1, "prompt": 123, "completion": 45, "total": 168})
        self.assertEqual(sess.last_usage["total"], 168)

    def test_web_state_carries_stats(self):
        harness.record_usage({"prompt_tokens": 8, "completion_tokens": 2})
        st = harness.load_stats()
        self.assertEqual(st["calls"], 1)


class TestConsolidate(unittest.TestCase):
    """#1 合并/冲突处理：聚类 → 模型判断 → 按决策改库。"""

    def setUp(self):
        reset()
        self._llm = harness.llm

    def tearDown(self):
        harness.llm = self._llm

    def _fake(self, payload):
        harness.llm = lambda msgs, **kw: json.dumps(payload, ensure_ascii=False)

    def test_groups_only_overlapping_entries(self):
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色，别太白。")
        harness.save_memory("深色主题2", "界面还是深色好", "user", "界面要用深色主题，白色刺眼。")
        harness.save_memory("qwen版本", "本地模型是 qwen2.5:7b", "reference",
                            "Ollama 里装的是 qwen2.5:7b，context 32k。")
        groups = harness.similar_groups()
        names = [sorted(e["name"] for e in g) for g in groups]
        self.assertIn(["深色主题", "深色主题2"], names)
        self.assertTrue(all("qwen版本" not in g for g in names), "不相干的不该被凑成一组")

    def test_merge_creates_new_and_supersedes_old(self):
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色，别太白。")
        harness.save_memory("深色主题2", "界面还是深色好", "user", "界面要用深色主题，白色刺眼。")
        self._fake({"action": "merge",
                    "merged": {"name": "深色界面", "description": "界面一律深色",
                               "body": "界面用深色，白色刺眼。", "type": "user"},
                    "reason": "说的是同一件事"})
        res = harness.consolidate(apply=True)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["applied"], "merge")
        self.assertIn("深色界面", harness.find_memory("深色界面")[0])
        for old in ("深色主题", "深色主题2"):
            self.assertEqual(harness.status_of(harness.find_memory(old)[1]), "superseded")
        names = harness.retrieve("深色界面")["names"]
        self.assertIn("深色界面", names)
        self.assertNotIn("深色主题", names)

    def test_supersede_keeps_newest_when_model_forgets_name(self):
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色，别太白。")
        harness.save_memory("深色主题2", "界面还是深色好", "user", "界面要用深色主题，白色刺眼。")
        self._fake({"action": "supersede", "reason": "后者更准确"})
        res = harness.consolidate(apply=True)
        self.assertEqual(res[0]["applied"], "supersede")
        status = {n: harness.status_of(harness.find_memory(n)[1]) for n in ("深色主题", "深色主题2")}
        self.assertEqual(list(status.values()).count("active"), 1)

    def test_keep_does_nothing(self):
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色，别太白。")
        harness.save_memory("深色主题2", "界面还是深色好", "user", "界面要用深色主题，白色刺眼。")
        self._fake({"action": "keep", "reason": "细节不同"})
        before = sorted(f for f in os.listdir(harness.MEM_DIR) if not f.startswith("."))
        res = harness.consolidate(apply=True)
        self.assertEqual(res[0]["action"], "keep")
        self.assertNotIn("applied", res[0])
        after = sorted(f for f in os.listdir(harness.MEM_DIR) if not f.startswith("."))
        self.assertEqual(after, before)

    def test_broken_json_does_not_crash(self):
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色，别太白。")
        harness.save_memory("深色主题2", "界面还是深色好", "user", "界面要用深色主题，白色刺眼。")
        harness.llm = lambda msgs, **kw: "我觉得挺好的"
        res = harness.consolidate(apply=True)
        self.assertEqual(res[0]["action"], "keep")
        self.assertIn("判不动", res[0]["reason"])


def _fake_vec(text):
    """3 维假向量：深色/界面、qwen/模型、会议/日程 各占一维。

    这样「深色」和「暗色」余弦 = 1.0，测试不用真下载 embedding 模型。
    """
    groups = (("深色", "暗色", "黑色", "护眼", "界面"),
              ("qwen", "模型", "版本", "ollama"),
              ("会议", "日程", "时间", "安排"))
    low = (text or "").lower()
    vec = [float(sum(low.count(w) for w in g)) for g in groups]
    if not any(vec):
        vec = [0.1, 0.1, 0.1]
    return vec


class _FakeEmbedServer:
    """本机假的 /embeddings 接口，回确定性向量并记录被调用了几次。"""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        self.calls = 0
        self.texts = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                text = str(obj.get("input", ""))
                outer.calls += 1
                outer.texts.append(text)
                body = json.dumps({"data": [{"embedding": _fake_vec(text)}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


class TestSemantic(unittest.TestCase):
    """v2.12.0 语义检索：embedding 只是加分项，没配就还是纯关键词。"""

    def setUp(self):
        reset()
        self._old = (harness.EMBED_MODEL, harness.EMBED_BASE_URL, harness.EMBED_WEIGHT)
        harness.EMBED_MODEL = "fake-embed"
        harness.EMBED_WEIGHT = 0.6

    def tearDown(self):
        harness.EMBED_MODEL, harness.EMBED_BASE_URL, harness.EMBED_WEIGHT = self._old

    def test_recalls_semantic_match_without_shared_words(self):
        """「喜欢暗色」和「界面用深色」一个词都不重叠，靠向量也要能找回来。"""
        with _FakeEmbedServer() as srv:
            harness.EMBED_BASE_URL = srv.url
            harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色，白色刺眼。")
            harness.save_memory("qwen版本", "本地模型", "reference", "Ollama 里是 qwen2.5:7b。")
            names = [e["name"] for _, e in harness.rank_entries("喜欢暗色")]
        self.assertIn("深色主题", names, f"语义应该能召回，实际 {names}")
        self.assertNotIn("qwen版本", names)

    def test_embedding_down_falls_back_to_keywords(self):
        harness.EMBED_BASE_URL = "http://127.0.0.1:9/v1"      # 肯定连不上
        harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色。")
        ranked = harness.rank_entries("深色")
        self.assertTrue(ranked, "embedding 挂了也该能用关键词搜到")
        self.assertEqual(ranked[0][1]["name"], "深色主题")

    def test_vector_cache_avoids_recomputing(self):
        with _FakeEmbedServer() as srv:
            harness.EMBED_BASE_URL = srv.url
            harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色。")
            harness.build_index()
            first = srv.calls
            self.assertGreaterEqual(first, 1)
            harness.build_index()                              # 文本没变
            self.assertEqual(srv.calls, first, "同样的文本不该重新算向量")

    def test_vectors_file_is_not_a_memory(self):
        with _FakeEmbedServer() as srv:
            harness.EMBED_BASE_URL = srv.url
            harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色。")
            harness.build_index()
        self.assertTrue(os.path.exists(harness._vec_path()))
        names = [meta.get("name", stem) for stem, meta, _body, _path in harness.read_all()]
        self.assertNotIn(".vectors.json", names)

    def test_stopwords_do_not_take_part_in_scoring(self):
        """「什么/怎么」这种虚词切出来 df=1、IDF 很高，会把不相干的记忆顶上来。"""
        today = harness.datetime.date.today().isoformat()
        write_mem("a.md", "写作风格", "文档别客套", "feedback", today, "不要客套话")
        write_mem("b.md", "工作环境", "本机是 Windows", "reference", today, "Windows 11 和 PowerShell")
        harness.build_index()
        q = harness.tokenize("工作电脑装了什么模型")
        self.assertNotIn("什么", q, "虚词应被过滤")
        self.assertIn("工作", q)
        ranked = harness.rank_entries("工作电脑装了什么模型")
        self.assertEqual(ranked[0][1]["name"], "工作环境", [e["name"] for _, e in ranked])

    def test_index_records_embed_model(self):
        with _FakeEmbedServer() as srv:
            harness.EMBED_BASE_URL = srv.url
            harness.save_memory("深色主题", "界面用深色", "user", "界面一直用深色。")
            idx = harness.build_index()
        self.assertEqual(idx["embed_model"], "fake-embed")
        harness.EMBED_MODEL = "another-embed"                 # 换了模型，旧索引要重建
        self.assertNotEqual(harness.load_index().get("embed_model"), "fake-embed")


class TestJournalRecall(unittest.TestCase):
    """压缩掉的对话原文必须还能被检索到——不然「永久记忆」只是存着够不着。"""

    def setUp(self):
        reset()
        os.makedirs(harness.JOURNAL_DIR, exist_ok=True)
        harness._journal_write("c1", "用户: 那个便宜方案最后定了 PostgreSQL，一个月 30 块\n助手: 记下了")
        harness._journal_write("c2", "用户: 明天要交周报\n助手: 好")
        harness.load_journal_index(force=True)

    def test_compacted_dialogue_is_searchable(self):
        r = harness.retrieve("PostgreSQL 便宜方案")
        self.assertIn("c1", r["blocks"], r.get("journal"))
        self.assertIn("PostgreSQL", r["journal"])
        self.assertIn("c1", r["journal"])

    def test_unrelated_block_not_dragged_in(self):
        r = harness.retrieve("PostgreSQL 便宜方案")
        self.assertNotIn("c2", r["blocks"])

    def test_journal_index_rebuilds_after_new_block(self):
        before = harness.load_journal_index()
        harness._journal_write("c3", "用户: 顺便说下 GPU 温度有点高\n助手: 好")
        after = harness.load_journal_index()
        self.assertNotEqual(before["signature"], after["signature"])
        self.assertIn("c3", [e["block"] for e in after["entries"]])

    def test_journal_hits_sit_beside_memories(self):
        harness.save_memory("写周报", "每周五行", "project", "周报默认在周五下午交。")
        r = harness.retrieve("周报 PostgreSQL 便宜方案")
        self.assertEqual(r["tier"], "digest")       # 记忆库也有命中
        self.assertIn("写周报", r["names"])
        self.assertIn("c1", r["blocks"])            # 原文块照样带进来


class TestSummaryTiering(unittest.TestCase):
    """摘要要做二级压缩：prompt 里的摘要稿不能无限长，原文块一个字都不删。"""

    def setUp(self):
        reset()
        os.makedirs(harness.JOURNAL_DIR, exist_ok=True)

    def test_long_summary_gets_tiered(self):
        summary = "".join("- [c%d] 第 %d 块：决定用 X%d，理由是便宜。\n" % (i, i, i)
                          for i in range(1, 31))
        s = harness.ChatSession(budget=8000, summary=summary)
        self.assertTrue(s.tier_summary())
        lines = [l for l in s.summary.splitlines() if l.strip()]
        self.assertLessEqual(len(lines), harness.SUMMARY_KEEP_LINES + 1)
        self.assertIn("–", lines[0])
        self.assertIn("c1", lines[0])

    def test_short_summary_left_alone(self):
        s = harness.ChatSession(budget=8000, summary="- [c1] 一句话\n- [c2] 两句话\n")
        self.assertFalse(s.tier_summary())
        self.assertEqual(s.summary, "- [c1] 一句话\n- [c2] 两句话\n")

    def test_tiering_keeps_recent_lines_verbatim(self):
        summary = "".join("- [c%d] 第 %d 块要点\n" % (i, i) for i in range(1, 26))
        s = harness.ChatSession(budget=8000, summary=summary)
        s.tier_summary()
        self.assertIn("- [c25] 第 25 块要点", s.summary)

    def test_prepare_caps_oversized_summary(self):
        s = harness.ChatSession(budget=8000, summary="- [c1] " + "长" * 9000)
        msgs, _, _ = s.prepare("你好")
        self.assertLess(len(msgs[0]["content"]), 9000)
        self.assertIn("已折叠", msgs[0]["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(shutil.which("git"), "需要 git 才能测记忆目录的版本历史")
class TestMemoryGit(unittest.TestCase):
    """memories/ 的改动要能翻回去：删错、改坏、被顶掉都还有据可查。"""

    def setUp(self):
        reset()
        _force_rmtree(harness.memgit_path())
        self._mode = harness.CFG.get("memory_git")
        harness.CFG["memory_git"] = "auto"

    def tearDown(self):
        harness.CFG["memory_git"] = self._mode

    def _head(self):
        return harness._memgit_run("rev-parse", "HEAD").stdout.strip()

    def test_save_and_delete_are_both_committed(self):
        harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        self.assertIn("保存 深色主题", harness.memgit_log(10))
        harness.delete_memory("深色主题")
        self.assertIn("移到回收站 深色主题", harness.memgit_log(10))

    def test_bad_edit_can_be_rolled_back(self):
        name, path = harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        sha = self._head()
        harness._atomic_write(path, harness._read_text_file(path) + "手滑改坏的一行\n")
        harness.rebuild_index()
        harness.memgit_commit("改坏了")
        harness.memgit_restore_file(sha, name)
        self.assertNotIn("手滑改坏", harness._read_text_file(path))
        self.assertIn("喜欢深色界面", harness._read_text_file(path))

    def test_cache_files_stay_out_of_history(self):
        harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        harness.touch_usage(["深色主题"])
        harness.retrieve("深色主题")
        listed = harness._memgit_run("ls-files", "-z").stdout
        self.assertIn("深色主题.md", listed)
        for junk in (".usage.json", ".index.json", ".vectors.json", ".stats.json"):
            self.assertNotIn(junk, listed)

    def test_switching_it_off_stops_recording(self):
        harness.save_memory("浅色主题", "偏好浅色", "user", "喜欢浅色界面。")
        before = harness.memgit_log(10)
        self.assertIn("浅色主题", before)
        harness.CFG["memory_git"] = "off"
        harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        harness.CFG["memory_git"] = "auto"        # 打开再读，才看得到这段时间没记新账
        after = harness.memgit_log(10)
        self.assertIn("浅色主题", after)
        self.assertNotIn("深色主题", after, "off 之后不该再记新账")
        # 关掉只是不记录，记忆本身照存
        self.assertIsNotNone(harness.find_memory("深色主题"))

    def test_history_can_be_filtered_by_name(self):
        harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        harness.save_memory("浅色主题", "偏好浅色", "user", "现在喜欢浅色。")
        only = harness.memgit_log(10, "深色主题")
        self.assertIn("深色主题", only)
        self.assertNotIn("浅色主题", only)

class TestManage(unittest.TestCase):
    """网页「管理」页要用的几个动作：回收站、原文块。"""

    def setUp(self):
        reset()

    def test_soft_deleted_memory_can_come_back(self):
        harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        harness.delete_memory("深色主题")
        self.assertIsNone(harness.find_memory("深色主题"))
        trash = harness.list_trash()
        self.assertEqual(len(trash), 1)
        self.assertIn("深色主题", trash[0]["name"])
        name = harness.restore_trash(trash[0]["file"])
        self.assertEqual(name, "深色主题")
        self.assertIsNotNone(harness.find_memory("深色主题"), "恢复后要能被检索到")
        self.assertEqual(harness.list_trash(), [], "恢复后回收站里不该还留着")
        self.assertEqual(harness.load_index()["entries"][0]["name"], "深色主题")

    def test_purge_is_the_only_real_delete(self):
        harness.save_memory("深色主题", "偏好深色", "user", "喜欢深色界面。")
        harness.delete_memory("深色主题")
        fn = harness.list_trash()[0]["file"]
        harness.purge_trash(fn)
        self.assertEqual(harness.list_trash(), [])

    def test_restore_refuses_path_tricks(self):
        # 两种分隔符都要挡：Linux 上 "..\\x.md" 并不是带路径的名字，光看 basename 会漏
        for bad in ("..\\深色主题.md", "../深色主题.md", "a/b.md", "..", ""):
            with self.assertRaises(ValueError):
                harness.restore_trash(bad)
            with self.assertRaises(ValueError):
                harness.purge_trash(bad)
        with self.assertRaises(FileNotFoundError):
            harness.purge_trash("不存在.md")

    def test_journal_text_only_accepts_real_blocks(self):
        harness._journal_write("c1", "用户：原方案是 MySQL 8\n助手：好的")
        blocks = harness.journal_blocks()
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["block"], "c1")
        self.assertIn("MySQL 8", harness.journal_text("c1"))
        for bad in ("c1; rm -rf", "../c1", "c", ""):
            with self.assertRaises(ValueError):
                harness.journal_text(bad)
        with self.assertRaises(FileNotFoundError):
            harness.journal_text("c99")

