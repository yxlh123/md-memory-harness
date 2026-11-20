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


if __name__ == "__main__":
    unittest.main(verbosity=2)
