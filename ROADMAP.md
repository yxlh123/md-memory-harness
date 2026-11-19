# 待办路线图（用户 2026-11-20 指定，下次更新再做）

> 状态：**只记录，未动代码**。当前版本仍是 v2.5.0。
> 四项都由用户提出，以下是原始意图 + 落地要点。按依赖关系排序见文末。

## 1. 记忆合并 / 冲突处理（consolidate）

现在只有写入时的 `_already_known()` 去重（检索最高分 ≥ 0.9 才拦），库一大必然出现：

- 同一事实换个说法存了两遍
- 「用户喜欢深色」和三个月后「用户改用浅色」并存，检索时新旧一起返回

做法：新增 `harness.py consolidate` 子命令

1. 用现成索引（`memories/.index.json` 的 tokens）做相似条目聚类
2. LLM 逐簇判断：**合并 / 新覆盖旧 / 都保留**
3. **先给建议，人工确认再执行**（不自动改库）

## 2. supersede 机制（记忆的版本）

- frontmatter 增加 `status: active | superseded`
- 增加 `supersedes: <旧条目名>`
- 新增 `harness.py update <name> <新内容>`，自动把旧条目标 `superseded` 并写 `supersedes`
- 检索层（`rank_entries` / `retrieve`）跳过 `superseded` 条目，但保留文件便于追溯

作用：这是 #1 的地基；也顺手解决「偏好变了旧记忆阴魂不散」。
注意：老条目没有 `status` 字段 → 迁移时按 `active` 兜底；`build_index()` 的 `_signature()` 要考虑 frontmatter 变化（mtime 已覆盖）。

## 3. 检索加权（TF-IDF 化）

现在 `rank_entries()` 是纯命中率：「用户喜欢」这种到处都有的 bigram 和「qwen2.5」这种专属词**同权重**。

- 加 IDF 权重（索引里已存全部 tokens，统计文档频率即可，**零依赖**）
- 轻微时间新近加成（`created` 字段已有）
- `user` / `feedback` 类型加权

评价：改动最小、检索提升最直接的一项，建议先做。

## 4. 语义检索（本地 embedding）

二元组匹配认死词：「喜欢暗色」搜不到「深色主题偏好」。

- Ollama 自带 embeddings 接口（`nomic-embed-text` / `bge-m3`，本地免费）
- `.index.json` 里存向量，余弦相似度与关键词分数混合
- **纯 Python 算余弦，不装任何东西**，守住零依赖底线

注意：`.index.json` 结构会变 → 加 `schema` 版本号，旧索引直接重建；`config.json` 增加 `embed_model`，没配就退回关键词检索。

## 建议顺序

1. **#3 TF-IDF**（独立、当天见效）
2. **#2 supersede**（#1 的地基）
3. **#1 consolidate**（依赖 #2）
4. **#4 embedding**（独立，但动 index 结构，放最后最稳）

## 共同要求

- 全部保持**纯标准库**，不引第三方依赖
- 每项都补离线单测（现在 39 个），README 同步
- 改完照旧：本地测试 → commit → `.scratch\gh_push_single.py` 推送 → CI 看绿
- 用户偏好：先给结论再讲理由；英文术语要带中文解释
