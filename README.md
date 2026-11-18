# md-memory-harness

一个**只用 Python 标准库**的本地 Markdown 记忆库：把记忆存成一个个 `.md` 文件，聊天时按需检索，对话变长就自动摘要、原文留档。带的网页界面和命令行都能用。

它解决的问题很小也很具体：**大多数"本地记忆"脚本其实是把整个记忆库塞进 prompt，聊久了再把对话一截了之**。记忆一多就顶不住，一截断就忘事。

这个项目的做法是把"上下文"拆成三层，各管一段：

```
聊天历史（短期） ──超预算──> 压成摘要（带块号）──> 原文落盘 journal/，随时回读
                                    │
记忆库（长期） ──索引+检索──> 只取相关的几条（三级回退，绝不全量硬塞）
```

## 特性

- **一条记忆一个 `.md`**：`frontmatter` + 正文，可以直接用编辑器改，也可以 `git` 管版本。
- **原子写**：先写临时文件再改名，断电或 Ctrl+C 不会留下半个文件。
- **软删除**：默认移进 `memories/.trash/`，删错了还能捞回来；`--purge` 才是真删。
- **中文可搜**：中文按二元组（bigram）切分建索引，`主题偏好` 和 `偏好主题` 都能命中（旧版子串匹配做不到）。
- **三级回退检索**：命中明确给摘要 + 开头，命中偏弱给命中词附近的片段，完全没命中就只给最近几条并明确标注——不会因为"没搜到"就把整个库倒进 prompt。
- **对话摘要压缩**：历史超过字符预算时，把较早的对话压成摘要，原文写进 `journal/c1.md`、`c2.md`…，摘要里带块号，`log show c1` 可回读全文。
- **可换模型**：任何 OpenAI 兼容接口都行（本地 Ollama / 智谱 / DeepSeek / vLLM…）。
- **两个界面**：命令行 `harness.py`，网页 `webui.py`（标准库 `http.server`，只监听 `127.0.0.1`）。

## 快速开始

需要 Python 3.9+，无第三方依赖。

```bash
git clone <this-repo> md-memory-harness
cd md-memory-harness
cp config.example.json config.json     # Windows: copy config.example.json config.json
```

`config.json` 可以什么都不填（默认走智谱 `glm-4-flash`，需 `api_key`），也可以指向本地模型：

```json
{
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "qwen2.5:7b",
  "api_key": "",
  "retrieve_budget": "6000",
  "history_budget": "8000"
}
```

```bash
python harness.py save "我习惯用 PowerShell，回答先给结论"   # 调 LLM 起名/写摘要
python harness.py list
python harness.py search "结论"          # 换词序也能搜到
python harness.py ask "我有什么偏好"
python harness.py chat                    # 连续对话，超预算自动摘要
python webui.py                           # 网页版，默认 http://127.0.0.1:8765
```

Windows 上可以直接双击 `ui.bat` / `chat.bat`。

先跑通再说模型：`python harness.py save "..." --no-llm` 不调接口，纯本地保存。

## 命令

| 命令 | 作用 |
| --- | --- |
| `save <内容> [--type user\|feedback\|project\|reference] [--no-llm]` | 存一条记忆，默认让模型起名/写描述/判类型 |
| `chat [--budget N]` | 连续对话；`/save`、`/search`、`/list`、`/history`、`/new`、`/exit` |
| `ask <问题>` | 单次问答（检索记忆后提问） |
| `list` | 列出全部记忆 |
| `search <词>` | 按索引搜索，按命中率排序 |
| `show <名字>` | 查看全文（文件名或 frontmatter 的 `name` 都认） |
| `delete <名字> [--purge]` | 软删除到 `.trash/`；`--purge` 彻底删 |
| `log [c1]` | 列出对话压缩块 / 回读某块全文 |
| `import <文件...> [--type T] [--no-llm]` | 导入 `.txt`/`.md`；UTF-8 和 GBK 都认 |
| `rebuild` | 重建 `MEMORY.md` 和 `.index.json` |
| `config [set <key> <value>]` | 查看/写入配置 |

网页端接口：`GET /api/state`，`POST /api/{chat,save,search,delete,import,config}`。

## 记忆文件长这样

`memories/回答偏好.md`：

```markdown
---
name: 回答偏好
description: 用户希望先给结论，英文术语要带中文解释
type: user
created: 2026-11-18
---

回答先给结论，再展开理由。英文术语第一次出现时带一句中文解释。
```

`type` 取值：`user`（关于用户）、`feedback`（纠正/偏好）、`project`（项目里的事）、`reference`（外部资料）。

`memories/MEMORY.md` 是给人看的目录，`memories/.index.json` 是给程序用的索引（都会自动重建，可以放心删）。

## 检索是怎么做的

`search` / `ask` 不比对整段文本，而是比对**词元集合**：

1. 中文按二元组切（`深色主题` → `深色`、`色主`、`主题`），英文按空白/标点切词。
2. 每条记忆建一份 token 集合，连同文件 `mtime`+`size` 存进 `.index.json`；文件变了才重建。
3. 打分：`命中 token 数 / 问题 token 数`。命中名字或描述的再加权，整词出现在名字/描述里再补一点。
4. 按分数三级回退：
   - `digest`（≥0.5）：摘要 + 开头一段正文，命中的记忆最多 6 条；
   - `snippet`（0.15~0.5）：摘要 + 命中词附近的片段；
   - `recent`（更低或没命中）：只给最近 8 条，开头明确写"没检索到相关记忆，以下是最近的几条"。
5. 总量受 `retrieve_budget` 限制（默认 6000 字符），超了按分数从低往高砍。

这样"记忆库很大"和"prompt 很小"就不冲突了：库多大都行，进 prompt 的永远是相关的那一小撮。

## 对话压缩是怎么做的

`ChatSession` 维护 `messages` 和 `summary`：

- 每次问答后检查历史总字符数，超过 `history_budget`（默认 8000）就把**除最近 4 条以外**的部分：
  1. 原文写入 `journal/c1.md`（逐块递增，不覆盖）；
  2. 让模型压成 3~6 行摘要，失败则退化为"头 2 条 + 尾 2 条"式的本地摘要；
  3. 摘要以 `- [c1] …` 的形式追加到 `summary`，拼进 system prompt；
- 摘要里的块号永远指向真实文件：`python harness.py log show c1` 能看回原文。**摘要是有损的，但原文没丢。**

网页端把 `history` / `summary` / `blocks` 放在浏览器里来回传，所以刷新之前上下文不会丢；服务端每次都做同样的压缩判断。

## 配置

优先级：环境变量 > `config.json` > 内置默认。

| 键 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| `base_url` | `MEMORY_LLM_BASE_URL` | 智谱 `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容地址（带 `/v1`） |
| `api_key` | `MEMORY_LLM_API_KEY` | 空 | 本地服务可留空 |
| `model` | `MEMORY_LLM_MODEL` | `glm-4-flash` | 模型名 |
| `memory_dir` | `MEMORY_DIR` | `./memories` | 记忆目录 |
| `retrieve_budget` | — | `6000` | 每次带进 prompt 的记忆字符上限 |
| `history_budget` | — | `8000` | 对话历史字符上限，超了压摘要 |

也可以用 `python harness.py config set model qwen3:4b` 写入 `config.json`。

## 已知局限（都是刻意的取舍）

- **字面匹配，不是语义检索**：问"我喜欢什么风格"而记忆里只写"深色模式"，就搜不到，会走 `recent` 兜底。要语义得上向量库，那就不是"纯标准库单文件"了。
- **没有分词词典**：中文 bigram 对短查询够用，长句偶尔会虚高命中。
- **`api_key` 明文存在 `config.json`**：文件不进 git（`.gitignore` 已排除），但本机其他程序可读。在意的话用环境变量。
- **单进程单用户**：没有文件锁，同时开两个 `webui.py` 写同一条记忆可能互相覆盖。
- **摘要质量取决于模型**：模型越小摘要越糊，但原文在 `journal/` 里，随时能捞。

## 开发

```bash
python -m unittest discover -s tests -v     # 15 个用例，不联网
python tests/mock_llm.py 8791               # 起一个假模型，验证 LLM 路径
```

测试用 `.test-tmp/`（项目内）而不是系统临时目录，Windows 上被沙箱/权限拦的时候不用改代码。

## 许可

MIT，见 [LICENSE](LICENSE)。

v2.0 是在一份由 AI 生成的初版基础上重写的：保留了"本地 md + 命令行 + 网页端"的骨架，重做了存储（原子写/软删除）、检索（索引/三级回退）和对话（摘要压缩/块号回读）。
