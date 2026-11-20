# 待办路线图（用户 2026-11-20 指定）

> 状态：**未完成项只记录、未动代码**。已落地：#3（v2.6.0）、#6（v2.7.0）、#5（v2.8.0）、#2（v2.9.0）、#1（v2.10.0）、#9（v2.11.0）、#4（v2.12.0）、#7（v2.15.0），见文末「已完成」。
> 九项都由用户提出（#1–#4 与 #5–#9 都是 2026-11-20 一次性给的），以下是原始意图 + 落地要点。按依赖关系排序见文末。

## 1. 记忆合并 / 冲突处理（consolidate）—— ✅ 已完成（v2.10.0，2026-11-21）

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

## 4. 语义检索（本地 embedding）—— ✅ 已完成（v2.12.0，2026-11-21）

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

## 7. memories 目录 git 化 —— ✅ 已完成（v2.15.0，2026-11-21）

落地时改了个方向：**不**在 `memories/` 里塞 `.git`，而是在它旁边放 `.memory-history/`，用
`git --git-dir=.memory-history --work-tree=memories` 操作。理由：`memories/` 很可能已经在某个项目仓库里，
嵌套一个 `.git` 会让外层仓库把它当 gitlink，既难解释也容易踩坑。

- 存、软删、彻底删、supersede、恢复、从历史写回——各提交一次；`memory_git=off` 可关，没装 git 就静默跳过
- 缓存文件（`.index.json`/`.vectors.json`/`.usage.json`/`.stats.json`/`.trash/`）写进 `info/exclude`，历史里只有记忆本身
- 新增 `history [--name] [--show]` 与 `recover <提交号> <名字>`：删掉的文件也能从历史里捞回来
- 5 个离线用例（`TestMemoryGit`），需要机器上有 git，没有就 skip
- 踩过的坑：`MEM_DIR` 带 `..` 时 git 报 `Invalid path`，必须 `normpath`；`subprocess` 用 `text=True` 会按 GBK 解码中文提交信息，得显式 `encoding="utf-8"`；初始化失败会留下只有 `objects/` 的空壳目录，所以判断"是不是仓库"要看 `HEAD` 而不是目录存在

`save` / `delete` / `consolidate` 后自动 `git commit`（只提交 `memories/`）。

- 记忆的演化历史全程可审计、可回滚
- 能自然同步到别的机器（加个 `memory pull/push` 就成多机同步）
- 和「记忆库 = 本地 md」这个定位是天作之合

注意：用户可能把 `memories/` 放在已有仓库里 → 先探测 `git rev-parse --show-toplevel`，是子目录就只提交该子路径；没有仓库时可选 `git init` 或干脆跳过（配置开关 `git_autocommit`）。

## 8. 网页端补管理面 —— ✅ 已完成（v2.16.0，2026-11-21）

现在网页只能聊天、看记忆列表、改配置，缺三块：

- **回收站**：浏览 `memories/.trash/`，支持恢复（`restore`）与彻底删除
- **journal 原文块**：点摘要里的 `[c1]` 能直接看那一块的原文（现在只能命令行 `log show c1`）
- **图片库**：浏览 `journal/images/`，看历史发过的图

## 9. token 用量统计 —— ✅ 已完成（v2.11.0，2026-11-21）

LLM 响应里的 `usage` 字段现在被扔掉了。

- 每次调用记录 `prompt_tokens` / `completion_tokens`，累计到 `memories/.stats.json`（原计划 `journal/.usage.json`，落地时放到记忆目录旁边，跟 `.usage.json` 作伴）
- 网页顶栏显示累计，回答下面显示「这一轮花了多少」
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

### v2.16.0（2026-11-21）—— #8 网页管理面

- `harness.py`：`list_trash()` / `restore_trash()` / `purge_trash()`、`journal_blocks()` / `journal_text()`、`list_images()`。
- `webui.py`：GET `/api/trash`、`/api/journal[?block=c1]`、`/api/images`、`/images/<file>`、`/api/history`；POST `/api/trash`（restore/purge）、`/api/recover`。
- `ui.html`：第四个标签「管理」，四张卡片（回收站 / 原文块 / 图片库 / 版本历史）。
- `memgit_commit()` 首次建库时先提交一次「首次记录」，历史不会从半路开始。
- 测试：`TestManage` 4 例（软删能捞回并重建索引、purge 才是真删、路径穿越被拒、`journal_text` 只认 `cN`），**94 个全绿**。
- 硬约束：管理页只把已有能力搬到网页，**没有任何自动删除路径**；彻底删必须人工点两次确认。


### v2.15.0（2026-11-21）—— #7 记忆目录版本历史

- 用户前提不变：「永久记忆不能动，除非人工剔除」——所以版本历史是**加保护**，不是加清理：删记忆只删当前文件，历史里那版还在，`recover` 能写回来
- `memories/` 旁边一个独立 git 仓库 `.memory-history/`，`git --git-dir ... --work-tree memories` 操作，不嵌套、不影响项目仓库
- 自动提交时机：`save_memory` / `delete_memory`（软删与 purge 分开写）/ `supersede_memory` / `restore_memory` / `memgit_restore_file`
- CLI：`history`、`history --name`、`history --show <sha>`、`recover <sha> <名字>`；配置项 `memory_git=auto|off`
- 94 个用例全绿（新增 `TestMemoryGit` 5 个，git 不存在时自动 skip）

### v2.14.0（2026-11-21）—— 压缩掉的对话可检索 + 摘要分层

- 用户拍板：「永久记忆这块不能动，这是这个项目的初心；剩下的你可以看着优化」→ 记忆条目与 `journal/` 原文**一律不设上限**，只管"每轮塞进 prompt 的那部分"
- `journal/` 单独建索引（`journal/.journal-index.json`，按 `文件名:size` 签名，变了才重建），`rank_journal()` 用同一套 IDF 打分（门槛 0.35，比记忆高），`_journal_snippet()` 摘命中最多的一行（160 字内）
- `retrieve()` 返回值新增 `journal` / `blocks`：记忆命中之外再带 1~2 段原文片段；记忆库全空时直接走 `tier="journal"`
- `prepare()` 把原文片段标成「压缩前的原话，和摘要冲突时以这里为准」
- 摘要二级压缩：`SUMMARY_MAX_LINES=20` / `SUMMARY_KEEP_LINES=12` / `SUMMARY_CAP=6000`，`tier_summary()` 把最老的一批合成 `- [c1–c12] …`（块号区间保留），原文一个不删
- 为什么：16K 上下文中记忆 6000 字 + 历史 8000 字之后只剩约 6700 token，每块摘要约 40 token，一百多块后每轮开始挤——压的只是"每轮要发出去的那份稿"
- 新增 8 个用例（`TestJournalRecall` 4、`TestSummaryTiering` 4），共 **85 个全绿**
- `print_search()` 和 `/api/search` 也会带上原文命中（`journal_text` / `journal_blocks`）

### v2.11.0（2026-11-21）—— #9 token 用量统计

- `llm()` 拿到 `usage` 不再扔：`record_usage()` 归一后累计进 `memories/.stats.json`（总量 + 按天）；`load_stats()` / `stats_totals()` 读回来
- `_norm_usage()` 认 `prompt_tokens`/`completion_tokens` 与 `input_tokens`/`output_tokens` 两套命名，认不出就返回 `{}`（不瞎猜）
- 流式请求带 `stream_options: {"include_usage": true}`；服务端 400 就自动退回不带它的请求——智谱实测认这个参数，最后一帧带完整 usage
- `ChatSession.ask()` / `ask_stream()` 用**调用前后的总量差**算这一轮的花费（`_usage_delta()`），所以含回答、自动捕获判断、超预算摘要这些所有调用；结果放进 `r["usage"]` 与 `self.last_usage`
- 命令行每轮回答后打 `· ↑350 ↓23 = 373 tokens（2 次调用）`；新增 `stats` 子命令看累计与最近 7 天
- 网页端：`/api/chat` 与 `/api/chat_stream` 的返回里带 `usage`，回答下面补一行；header 加 `token` 徽章（悬停看按天明细）；`/api/state` 带 `stats`
- 测试新增 `TestUsageStats` 5 个（两套字段归一化、累计与按天、`.stats.json` 不被当记忆、`ask` 回报本轮用量、stats 可读），共 **71 个全绿**
- 实测智谱 `glm-4v-flash`：命令行 `↑147 ↓29 = 176 tokens`；网页流式一轮 `↑350 ↓23 = 373 tokens（2 次调用）`

### v2.13.0（2026-11-21）—— 语义检索开关 + 虚词过滤

- 设置页把「语义检索 embed_model」输入框换成**开关（CSS 滑块）+ 状态文字 + 模型名**：`setEmbed(on)` 调 `/api/config` 写 `embed_model`，打开写模型名、关掉写空串；`renderAll()` 回填 `checked` 与「开 · 模型名」/「关（只用关键词）」
- 新增 `STOPWORDS`（约 90 个中文 bigram + 英文常见词：什么/怎么/这个/一下/可以/我们/但是/如果/已经/没有/不是/就是 the/and/for/what/how…），`tokenize()` 里中英文都过一遍
  - 起因是真数据上的怪结果：问「工作电脑装了什么模型」，「写作风格」被顶到第一——中文虚词 bigram 在库里只出现过一两次，`df=1` → IDF 极高 → 一条毫不相干的记忆拿满分
  - 修后同一个问句：「工作环境」1.48 第一
- `SEMANTIC_ONLY` 0.55 → **0.45**，并写进 README：这个阈值**跟着模型走**，不是通用常数
- 实测对比两个模型的噪声底（4 条文档 × 8 组查询）：

  | 模型 | 维度 | 相关句 cos | 无关句 cos | 结论 |
  |---|---|---|---|---|
  | `nomic-embed-text`（270MB） | 768 | 0.30~0.73，均值 0.53 | 0.36~0.67，均值 0.56 | 中文**分不开**：无关句比有的相关句还高 |
  | `qwen3-embedding:0.6b`（600MB） | 1024 | 0.46~0.64，均值 0.57 | 0.25~0.41，均值 0.31 | 界限清楚，0.45 阈值干净 |

  - 结论：**中文用 `qwen3-embedding:0.6b`**，`EMBED_DEFAULT` 与 README 都改它；nomic 只在英文记忆上稳
  - nomic + 0.62 阈值时仍会误召回（「我电脑上跑什么模型」→「回答偏好」0.67），换 qwen3 后同一问句不再误召
- 测试新增 `test_stopwords_do_not_take_part_in_scoring`（虚词被滤、真词保留、排序正确），共 **77 个全绿**
- 真模型验收（临时库两条记忆）：语义开 →「暗一点看着舒服」召回「界面主题」0.65；语义关 → 一条都没有

### v2.12.0（2026-11-21）—— #4 可选语义检索（embedding）

- 配置新增 `embed_model` / `embed_base_url`（默认本机 Ollama）/ `embed_api_key` / `embed_weight`（默认 0.6）；**`embed_model` 为空时行为与 v2.11.0 完全一致**
- `embed_text(text)` 走 OpenAI 兼容的 `POST /embeddings`，同时认 `data[0].embedding` 与 Ollama 原生 `embedding`；失败一律返回 `None`（检索退回关键词，不报错）
- `_cosine(a, b)` 纯 Python；`.index.json` 升到 `INDEX_SCHEMA = 5`，每条可带 `vec`，并记 `embed_model`（换模型就整库重算）
- 向量缓存 `memories/.vectors.json`：按 `sha1(名字+描述+正文前 800 字)` 判断内容变没变，没变直接复用，不重复花钱
- `rank_entries()` 改成混合打分：命中时 `关键词分 + embed_weight × max(cos, 0)`；一条关键词都没中但 `cos ≥ SEMANTIC_ONLY = 0.55` 的按纯语义命中收进来（`cos × 0.9`）
- 网页端：`api_state()` 带 `embed_model`，设置页加「语义检索 embed_model」输入框
- 测试新增 `TestSemantic` 5 个（无共有词也能语义召回、embedding 挂了退回关键词、向量缓存不重算、`.vectors.json` 不当记忆、索引记录模型名），共 **76 个全绿**——用本机假 `/embeddings` 服务回 3 维确定性向量，不联网、不用真下载模型
- 现实约束：本机 Ollama 没装 embedding 模型（`ollama pull nomic-embed-text` 要下几百 MB），所以这功能默认关着，等用户愿意下模型再开

### v2.10.0（2026-11-21）—— #1 记忆合并 / 冲突处理（consolidate）

- `similar_groups(threshold=0.3, max_groups=8, max_size=4)`：只用 active 条目，按词元相似度做并查集聚类
- 相似度取 `_similarity() = max(Jaccard, 包含度)`——**只用 Jaccard 会漏**：实测两条「深色主题」记忆交集 5 个词，Jaccard 只有 0.25（<0.3 不聚类），包含度 0.45；"短的一条几乎包在长的一条里"正是半重复条目的典型形态
- `judge_group()`：每组甩给模型三选一 `merge` / `supersede` / `keep`，提示词里要求"拿不准就 keep"；模型回的不是 JSON 时降级成 `keep`（理由写「判不动」），不让整理流程崩
- `_apply_group()`：merge 生成新条目并把组里旧的都标 superseded；supersede 保留组里最新一条顶掉其余；模型忘了给正文就把各条 `- 正文` 拼起来，忘了给名字就保留最新一条；keep 什么都不动
- CLI `consolidate [--apply] [--threshold 0~1] [--no-llm]`，默认 dry-run 只打印建议；网页端「记忆」面板加「整理」按钮（先 dry-run 弹 confirm，确认后 apply）
- 测试新增 `TestConsolidate` 5 个（只聚重叠的、merge 新建并降级旧的、模型忘名字时保留最新、keep 不动库、坏 JSON 不崩），共 **66 个全绿**
- 实测：临时库里两条「深色主题」→ 模型判 merge → 新条目「界面使用深色主题」，两条旧的变 `⤳ 已被…取代`，文件都在；网页端 `/api/consolidate` 一次把三条（含一条自动捕获）判成一组

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
