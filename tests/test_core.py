#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心逻辑测试：不联网、不依赖真实模型。

    python -m unittest discover -s tests -v
"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
