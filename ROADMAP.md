# 待办路线图（用户 2026-11-20 指定）

> 状态：**未完成项只记录、未动代码**。已落地：#3（v2.6.0）、#6（v2.7.0）、#5（v2.8.0）、#2（v2.9.0），见文末「已完成」。
> 九项都由用户提出（#1–#4 与 #5–#9 都是 2026-11-20 一次性给的），以下是原始意图 + 落地要点。按依赖关系排序见文末。

## 1. 记忆合并 / 冲突处理（consolidate）

现在只有写入时的 `_already_known()` 去重（检索最高分 ≥ 0.9 才拦），库一大必然出现：

- 同一事实换个说法存了两遍
- 「用户喜欢深色」和三个月后「用户改用浅色」并存，检索时新旧一起返回

做法：新增 `harness.py consolidate` 子命令

1. 用现成索引（`memories/.index.json` 的 tokens）做相似条目聚类
2. LLM 逐簇判断：**合并 / 新覆盖旧 / 都保留**
3. **先给建议，人工确认再执行**（不自动改库）

## 2. supersede 机制（记忆的版本）—— ✅ 已完成（v2.9.0，2026-11-21）

- frontmatter 增加 `status: active | superseded`，以及 `supersedes` / `superseded_by` / `superseded_on`
- 落地命令不是原计划的 `update`，而是 `save --supersedes 旧名` 与 `supersede 旧名 新内容`（外加 `restore 名字` 反悔）
- 检索层 `rank_entries()` 跳过非 `active` 条目，`retrieve()` 自然也带不到；文件保留，`show` 可查、网页端可恢复
- 没有 `status` 字段的老条目按 `active` 兜底（`status_of()`）；`.index.json` 升到 `INDEX_SCHEMA = 4`，条目带 `status`/`superseded_by`
- 老名打错时只警告、不失败：新条目先落盘，`FileNotFoundError` 吞掉——保存一条记忆不该因为参数写错而整个丢掉

作用：这是 #1 的地基；也顺手解决「偏好变了旧记忆阴魂不散」。

## 3. 检索加权（TF-IDF 化）—— ✅ 已完成（v2.6.0，2026-11-21）

~~现在 `rank_entries()` 是纯命中率：「用户喜欢」这种到处都有的 bigram 和「qwen2.5」这种专属词**同权重**。~~

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

## 5. 记忆元数据补全 —— ✅ 已完成（v2.8.0，2026-11-21）

frontmatter 再加两个字段：

- `updated`：最后修改时间
- `source`：这条怎么来的（`网页表单` / `命令行 save` / `chat 自动捕获` / `导入 xxx.md` / `网页端确认建议`）

`last_used` 没写进 frontmatter，改成 `memories/.usage.json`（每条 `{"uses": N, "last": "YYYY-MM-DD"}`）——原计划里"攒批再落盘"就是因为写 md 会改 `mtime` 触发整库重建，那不如干脆不写 md。

作用是 #1（合并判定）和时间加权的数据基础；也让「哪条记忆从来没用过」可见——按 `uses` 一排序就知道哪些是死条目（`list` 与网页端都会显示）。

## 6. 流式输出（SSE）—— ✅ 已完成（v2.7.0，2026-11-21）

~~现在聊天要等模型**吐完整段才显示**，本机 7b 模型要等好几秒，体感像卡住。~~

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
- 每项都补离线单测（现在 45 个），README 同步
- 改完照旧：本地测试 → commit → `.scratch\gh_push_single.py` 推送 → CI 看绿
- 用户偏好：先给结论再讲理由；英文术语要带中文解释

## 已完成

### v2.9.0（2026-11-21）—— #2 supersede 机制

- `save_memory(..., supersedes="")`：新条目 frontmatter 写 `supersedes`，并调 `supersede_memory()` 把旧条目降级
- `supersede_memory(old, by)` / `restore_memory(name)` / `status_of(meta)` / `dump_memory(meta, body)` / `_set_meta(name, updates)`（值为 `None` 表示删键）
- `rank_entries()` 跳过 `status != active`；`.index.json` 升到 `INDEX_SCHEMA = 4`，每条带 `status` / `superseded_by`
- 命令行新增 `supersede <旧名> <新内容>`、`restore <名字>`，`save` 加 `--supersedes`；`list` 用 `⤳ 已被 X 取代` 标出来
- 网页端：`/api/supersede`、`/api/restore`；记忆列表里旧版淡色 + `旧版` 标签 + 「恢复」按钮
- 测试新增 `TestSupersede` 6 个（写入 supersedes、被取代的不进检索、restore 复活、索引 schema 4 带状态、旧名打错不丢新条目、网页端 supersede/restore 闭环），共 **61 个全绿**

### v2.8.0（2026-11-21）—— #5 元数据补全

- `save_memory(...)` 多一个 `source` 参数，frontmatter 写 `updated`（当天）与 `source`（有才写）；`do_save` / `import_text` / 自动捕获 / 网页端都按场景传来源
- 新增 `usage_path()` / `load_usage()` / `touch_usage()`：命中统计放 `memories/.usage.json`（`{"uses": N, "last": "YYYY-MM-DD"}`），**不写回 md**——写 md 会改 `mtime`，索引签名一变就整库重建
- `retrieve()` 命中后调 `touch_usage(names)`；`list` 与网页端记忆列表显示「被检索 N 次 / 从未被检索」
- 测试新增 `TestMeta` 5 个（`updated` 落盘、`source` 可省略、`touch_usage` 累加、`retrieve` 自动记一次、写统计不触发索引重建），共 **55 个全绿**

### v2.7.0（2026-11-21）—— #6 流式输出

- `harness.py` 新增 `llm_stream(messages, temperature)`：请求带 `"stream": true`，逐行解析 `data:` 的 `choices[0].delta.content`；对方忽略 stream、直接回整段 JSON 时整段当一次产出（`Content-Type` 不是 event-stream 就走这条）
- `ChatSession` 拆成 `prepare()`（检索 + 拼 prompt）/ `finish()`（入历史 + 压缩 + 记记忆）/ `ask()` / `ask_stream()`；流式版把最终结果放 `self.last_result`
- 命令行 `chat` / `ask` 默认逐字出字，加 `--no-stream` 回到整段显示；`ask` 流失败会自动退回 `llm()` 整段
- `webui.py` 新增 `POST /api/chat_stream`（SSE：`data: {"delta":…}` … `data: {"done":true,…state}`），`do_POST` 里把请求体读取抽成 `_read_json()` 复用；无 `Content-Length`、靠关连接表示流结束
- `ui.html` 新增 `streamChat()`（`fetch` + `ReadableStream` + `TextDecoder` 手动拼 `\n\n` 分帧），服务端没有该接口时自动退回 `/api/chat`
- 测试新增 `TestStreaming` 5 个：真起 SSE 假服务验解析、忽略 stream 的 JSON 回退、`ask_stream` 收尾入历史、流式也触发压缩、真起 `webui` 服务验 `/api/chat_stream` 事件结构。当时共 **50 个全绿**
- 实测智谱 `glm-4v-flash`：首个增量 0.43s，整段 2.37s（47 个增量）；命令行同样逐字

### v2.6.0（2026-11-21）—— #3 检索加权

- `.index.json` 升到 `INDEX_SCHEMA = 3`，新增 `n`（文档数）与 `df`（文档频率表）；旧版本索引自动重建
- `_idf(idx)`：`log(1 + (n+0.5)/(df+0.5))`，纯统计零依赖
- `rank_entries()` 改为 IDF 加权：分数 = 命中词权重 / 问题词总权重；生词不进分母；整句命中名字/描述 +0.5；再乘类型权重与 `_recency_boost()`（半衰期 120 天、最多 +15%）
- **拿掉了「命中名字额外加权」**：名字/描述的 token 本来就计入文档，再单独加权会让只命中「用户」这种高频词的条目盖过命中专有词的条目（新测试抓到的）
- 新增 6 个用例（稀有词优先、生词不稀释、新近破平、类型权重、旧索引重建、df 按文档计），共 45 个全绿
