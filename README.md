# md-memory-harness

[![tests](https://github.com/yxlh123/md-memory-harness/actions/workflows/tests.yml/badge.svg)](https://github.com/yxlh123/md-memory-harness/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

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
- **能发图片**：命令行 `--image` / `/img`，网页端点 📎 或直接 Ctrl+V 粘贴截图。图片落盘到 `journal/images/`，上下文里只留路径标记，base64 不进 md。
- **两个界面**：命令行 `harness.py`，网页 `webui.py`（标准库 `http.server`）。默认只监听本机，`--lan` 可让同一 WiFi 下的手机/平板打开，带口令保护。

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
| `chat [--budget N] [--image 图]` | 连续对话；`/save`、`/search`、`/list`、`/history`、`/img`、`/new`、`/exit` |
| `ask <问题> [--image 图]` | 单次问答（检索记忆后提问），可附图 |
| `list` | 列出全部记忆 |
| `search <词>` | 按索引搜索，按命中率排序 |
| `show <名字>` | 查看全文（文件名或 frontmatter 的 `name` 都认） |
| `delete <名字> [--purge]` | 软删除到 `.trash/`；`--purge` 彻底删 |
| `log [c1]` | 列出对话压缩块 / 回读某块全文 |
| `import <文件...> [--type T] [--no-llm]` | 导入 `.txt`/`.md`；UTF-8 和 GBK 都认 |
| `rebuild` | 重建 `MEMORY.md` 和 `.index.json` |
| `config [set <key> <value>]` | 查看/写入配置 |

网页端接口：`GET /api/state`，`POST /api/{chat,save,search,delete,import,config,model}`。

网页端启动参数：`python webui.py [端口] [--lan] [--host IP] [--token 口令] [--no-open]`。

## 图片

只对**支持读图的模型**有效（qwen3-vl、qwen2.5-vl、glm-4v、gpt-4o、claude 之类）。模型不支持时接口会报错，`chat` 里会提示你换模型。

```bash
python harness.py ask "这张图里的报错是什么意思" --image .\errcode.png
python harness.py chat --image .\chat1.png      # 开场先挂一张
```

对话里随时挂图：

```text
你> /img C:\Users\me\Desktop\shot.png
(已挂 1 张图，下一句一起发)
你> 帮我看看这个界面哪不对
```

网页端点输入框左边的 📎，或者截完图直接在输入框里 Ctrl+V。

几个约定：

- 图片存到 `journal/images/<时间戳>-<随机>.png`，**原文里只写路径**（`[图片 journal/images/…]`），base64 不进 md，`grep` 也不会被几十兆的字符串卡住。
- 一张图按 `IMG_WEIGHT = 1200` 字符计入对话预算，压缩时图片和它的路径一起进 `journal/cN.md`；回读时看到的是路径，不是图。
- 单张上限 6MB、一条消息最多 4 张、请求体上限 24MB（`webui.py` 的 `MAX_IMAGE_BYTES` / `MAX_IMAGES` / `MAX_BODY`）。
- 图片以 data URL 形式发给模型，走的是 OpenAI 多模态格式 `content: [{type:"text"}, {type:"image_url"}]`；老接口只认纯文本的话这条路走不通。

## 手机 / 局域网访问

默认只监听 `127.0.0.1`，别的设备连不上。想用手机、平板、同一 WiFi 下的另一台电脑打开网页端：

```bash
python webui.py --lan
```

双击 `ui-lan.bat` 也行。启动后终端会打印几条地址：

```text
同一个 WiFi 下，手机/别的电脑用下面这个地址打开：
  http://192.168.1.23:8765/?k=x7k2mq9p

访问口令：x7k2mq9p（已经带在上面链接的 ?k= 里，别把链接发给别人）
```

把**带 `?k=` 的整条地址**发到手机上打开就行。几点说明：

- `--lan` 会把监听地址改成 `0.0.0.0`（所有网卡），并**自动生成一个 8 位口令**；也可以自己指定 `--token 我的口令`。
- 口令通过 `?k=` 或 `X-Token` 请求头传，网页端拿到 `?k=` 后会自动给每个请求带上，不用手动登录。
- 第一次运行 Windows 会弹防火墙提示，必须选**允许**（尤其是「专用网络」那一项），否则手机连不上。
- **这不是给公网用的**：没有账号体系、没有 HTTPS，口令就是唯一保护，同一网络里的人猜到或看到链接就能读你的全部记忆。只在自己家的 WiFi 里用。
- 想只绑某一个网卡：`python webui.py --host 192.168.1.23`；端口照老样子跟在后面：`python webui.py 9000 --lan`。

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
   - `digest`（最高分 ≥ 0.5）：摘要 + 开头一段正文，最多 6 条；
   - `snippet`（有命中但最高分 < 0.5）：摘要 + 命中词附近的原文片段，最多 6 条；
   - `recent`（一条都没命中）：只给最近 8 条，开头明确写"没检索到直接相关的记忆"。
5. 总量受 `retrieve_budget` 限制（默认 6000 字符），超了按分数从低往高砍。

这样"记忆库很大"和"prompt 很小"就不冲突了：库多大都行，进 prompt 的永远是相关的那一小撮。

## 对话压缩是怎么做的

`ChatSession` 维护 `messages` 和 `summary`：

- 每次问答后检查历史总字符数，超过 `history_budget`（默认 8000）就把**除最近 4 条以外**的部分：
  1. 原文写入 `journal/c1.md`（逐块递增，不覆盖）；
  2. 让模型压成 3 行以内的要点（保留结论、决定及理由、路径/命令/数字），失败则退化为"保留头 70% + 尾 30% 字符"的本地摘要；
  3. 摘要以 `- [c1] …` 的形式追加到 `summary`，拼进 system prompt，并**原子写进 `journal/.summary.md`**；
- 摘要里的块号永远指向真实文件：`python harness.py log show c1` 能看回原文。**摘要是有损的，但原文没丢。**

网页端把 `history` / `summary` / `blocks` 放在浏览器里来回传，所以刷新之前上下文不会丢；服务端每次都做同样的压缩判断。浏览器刷新或换台设备打开时，`history` 是空的，服务端会把磁盘里的 `journal/.summary.md` 和已有块号捡回来（`ChatSession.from_state`），接着上次的摘要往下聊——压缩过的内容不会因为关了网页就白压。

## 什么会自动落盘，什么要你动手

| 东西 | 位置 | 谁触发 |
| --- | --- | --- |
| 被压缩的对话原文 | `journal/c1.md`、`c2.md`… | **自动**（历史超 `history_budget` 就压） |
| 压缩摘要索引 | `journal/.summary.md` | **自动**（每次压缩后原子写；重开会话自动读回） |
| 图片 | `journal/images/<时间>-<随机>.png` | **自动**（发图时落盘，md 里只留路径） |
| 检索索引 | `memories/.index.json` | **自动**（惰性重建：记忆文件变了、或签名对不上就重扫） |
| 记忆条目 | `memories/<名字>.md` | `auto_save=auto` 时**自动**（每轮判断一次）；`suggest` 时先问你；`off` 时纯手动 |
| 记忆总览 | `memories/MEMORY.md` | **自动**（增删记忆时重建，只给人看） |

也就是说：`journal/` 是聊天留下的痕迹，`memories/` 是长期知识。默认 `auto_save=auto`，模型觉得"这句话里有该长期记住的东西"就直接写进 `memories/`；不想让它自作主张就切到 `suggest`（每轮弹一句"要记吗"）或 `off`（只存你手动的）。

## 记忆自动捕获（auto_save）

每轮回答完，会多一次很短的小调用，问模型："这一轮里有没有该长期记住的、关于用户本人的稳定信息？"

- **只认用户说的话**：偏好、习惯、身份、在做的项目、对你的长期要求。
- **不记**：一次性问题、临时数字、助手自己说的话、常识。
- 输出候选（最多 2 条），先跟已有记忆比对，太像就跳过（不会存重复条目）。

三档开关，网页「配置」页或 `config.json` 的 `auto_save` 都能改：

| 值 | 行为 |
| --- | --- |
| `auto`（默认） | 直接存进 `memories/`，聊天里回一句"🧠 已自动记住：xxx"，不想要就去「记忆」页删 |
| `suggest` | 不回存，只在回答下面弹一条"🧠 记一条？[保存][不用]" |
| `off` | 完全不猜，只存你手动 `save` 的 |

代价是多一次小调用（约 0.5 秒、几十 token）。嫌慢就设 `off`；觉得记太碎就设 `suggest` 过一遍眼。

## 配置

优先级：环境变量 > `config.json` > 内置默认。

| 键 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| `base_url` | `MEMORY_LLM_BASE_URL` | 智谱 `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容地址（带 `/v1`） |
| `api_key` | `MEMORY_LLM_API_KEY` | 空 | 本地服务可留空 |
| `model` | `MEMORY_LLM_MODEL` | `glm-4-flash` | 当前模型名 |
| `models` | — | 只有 `model` 一项 | 可选模型列表 `[{"id","label"}]`，网页端下拉用 |
| `memory_dir` | `MEMORY_DIR` | `./memories` | 记忆目录 |
| `retrieve_budget` | — | `6000` | 每次带进 prompt 的记忆字符上限 |
| `history_budget` | — | `8000` | 对话历史字符上限，超了压摘要 |
| `auto_save` | — | `auto` | 记忆自动捕获：`auto` / `suggest` / `off` |

也可以用 `python harness.py config set model qwen3:4b` 写入 `config.json`。

### 多个模型之间切换

`config.json` 里可以放一张 `models` 列表，把常用的模型都写上（`id` 后面跟个好看的 `label`）：

```json
"model": "glm-4v-flash",
"models": [
  {"id": "glm-4v-flash", "label": "看图 · 16K"},
  {"id": "glm-4-flash",  "label": "长文本 · 128K"}
]
```

之后有三个切换入口，改的都是同一份 `config.json`：

- 网页端右上角的下拉框（切完立刻生效，命令行下次运行也用它）；
- `python harness.py config set model glm-4-flash`；
- `POST /api/model {"model": "glm-4-flash"}`。

`models` 里的第一个会被当成默认展示顺序，当前 `model` 不在列表里时也会自动补进去。不写 `models` 就只有一个模型，行为跟以前一样。

> 小提示：同一家厂商的模型共用 `base_url`；要跨厂商（比如本机 Ollama ↔ 智谱）得连着 `base_url` 一起改，因为 `base_url` 是全局的一个值。

## 已知局限（都是刻意的取舍）

- **字面匹配，不是语义检索**：问"我喜欢什么风格"而记忆里只写"深色模式"，就搜不到，会走 `recent` 兜底。要语义得上向量库，那就不是"纯标准库单文件"了。
- **没有分词词典**：中文 bigram 对短查询够用，长句偶尔会虚高命中。
- **`api_key` 明文存在 `config.json`**：文件不进 git（`.gitignore` 已排除），但本机其他程序可读。在意的话用环境变量。
- **单进程单用户**：没有文件锁，同时开两个 `webui.py` 写同一条记忆可能互相覆盖。
- **摘要质量取决于模型**：模型越小摘要越糊，但原文在 `journal/` 里，随时能捞。
- **图片没做压缩**：6MB 的截图会原样存盘、原样发给模型，多轮带同一张图会重复计费；要省就自己先缩图。
- **局域网模式只挡手滑**：`--lan` 的口令是明文的、没有 HTTPS、没有账号锁，同一网络里有人抓包或者看到链接就能进。不要把它暴露到公网（别做端口映射）。

## 开发

```bash
python -m unittest discover -s tests -v     # 39 个用例，不联网
python tests/mock_llm.py 8791               # 起一个假模型，验证 LLM 路径
```

测试用 `.test-tmp/`（项目内）而不是系统临时目录，Windows 上被沙箱/权限拦的时候不用改代码。

## 下一步（还没做）

排期见 [ROADMAP.md](ROADMAP.md)：记忆合并/冲突处理（consolidate）、supersede 版本机制、TF-IDF 检索加权、本地 embedding 语义检索、记忆元数据补全、流式输出、memories 目录 git 化、网页管理面、token 用量统计。当前版本 `2.5.0` 不含这些，别按上面的说明去找这些功能。

## 许可

MIT，见 [LICENSE](LICENSE)。

v2.0 是在一份由 AI 生成的初版基础上重写的：保留了"本地 md + 命令行 + 网页端"的骨架，重做了存储（原子写/软删除）、检索（索引/三级回退）和对话（摘要压缩/块号回读）。
