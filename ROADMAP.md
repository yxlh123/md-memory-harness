# 待办路线图（用户 2026-11-20 指定，下次更新再做）

> 状态：**只记录，未动代码**。当前版本仍是 v2.5.0。
> 九项都由用户提出（#1–#4 与 #5–#9 都是 2026-11-20 一次性给的），以下是原始意图 + 落地要点。按依赖关系排序见文末。

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

## 5. 记忆元数据补全

frontmatter 再加三个字段：

- `updated`：最后修改时间
- `source`：指向产生它的 `journal` 块号（现在自动捕获的记忆**不知道出处**，没法回溯当时是怎么说的）
- `last_used`：检索命中时顺手写回

作用是 #1（合并判定）和 #3（时间加权）的数据基础；也让「哪条记忆从来没用过」可见——用 `last_used` 一排序就知道哪些是死条目。

注意：`last_used` 会在检索时写文件 → 频繁写盘，建议攒批（比如同一进程内合并、或每天落一次），别每命中一次就改 mtime。

## 6. 流式输出（SSE）

现在聊天要等模型**吐完整段才显示**，本机 7b 模型要等好几秒，体感像卡住。

- Ollama 和 OpenAI 协议都支持 SSE（Server-Sent Events，服务端逐条推）
- CLI 和网页都改成逐字出字
- 网页端用 `fetch` + `ReadableStream` 读 `data:` 行（不用 EventSource，因为要 POST）
- 注意配合压缩/自动捕获：流结束后再跑 `compact_if_needed()` 与 `capture_memory()`，别在流中间插

评价：纯体验向，改动集中在 `llm()` 与 `webui.py` 的转发，风险低、见效快。

## 7. memories 目录 git 化

`save` / `delete` / `consolidate` 后自动 `git commit`（只提交 `memories/`）。

- 记忆的演化历史全程可审计、可回滚
- 能自然同步到别的机器（加个 `memory pull/push` 就成多机同步）
- 和「记忆库 = 本地 md」这个定位是天作之合

注意：用户可能把 `memories/` 放在已有仓库里 → 先探测 `git rev-parse --show-toplevel`，是子目录就只提交该子路径；没有仓库时可选 `git init` 或干脆跳过（配置开关 `git_autocommit`）。

## 8. 网页端补管理面

现在网页只能聊天、看记忆列表、改配置，缺三块：

- **回收站**：浏览 `memories/.trash/`，支持恢复（`restore`）与彻底删除
- **journal 原文块**：点摘要里的 `[c1]` 能直接看那一块的原文（现在只能命令行 `log show c1`）
- **图片库**：浏览 `journal/images/`，看历史发过的图

## 9. token 用量统计

LLM 响应里的 `usage` 字段现在被扔掉了。

- 每次调用记录 `prompt_tokens` / `completion_tokens`，累计到 `journal/.usage.json`
- 网页顶栏显示「本次 / 今日累计」
- 接 `glm-4-flash` 这类有免费额度的模型时，心里有数

## 建议顺序

按「先见效、再打地基、最后动结构」排：

1. **#3 TF-IDF 加权** —— 独立，当天见效，检索立刻变准
2. **#6 流式输出** —— 独立，纯体验，用户每天都能感觉到
3. **#5 元数据补全** —— #1/#3 的数据基础，越早做越好
4. **#2 supersede** —— #1 的地基
5. **#1 consolidate** —— 依赖 #2 和 #5
6. **#9 token 用量** —— 小，随手做
7. **#4 embedding** —— 独立，但动 index 结构
8. **#7 memories git 化** —— 数据层改动，放后面稳
9. **#8 网页管理面** —— 纯界面，最后补

## 共同要求

- 全部保持**纯标准库**，不引第三方依赖
- 每项都补离线单测（现在 39 个），README 同步
- 改完照旧：本地测试 → commit → `.scratch\gh_push_single.py` 推送 → CI 看绿
- 用户偏好：先给结论再讲理由；英文术语要带中文解释
