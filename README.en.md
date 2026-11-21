# md-memory-harness

[简体中文](README.md) | English

[![tests](https://github.com/yxlh123/md-memory-harness/actions/workflows/tests.yml/badge.svg)](https://github.com/yxlh123/md-memory-harness/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

A local Markdown memory store built with **only the Python standard library**: memories are individual `.md` files, retrieved on demand during chat; when the conversation grows long it is summarized automatically and the original text is archived. Both the bundled web UI and the command line work.

The problem it solves is small and specific: **most "local memory" scripts actually stuff the entire memory store into the prompt, and when the chat gets long they simply truncate it**. Once there is a lot of memory it collapses; once it truncates, it forgets.

This project splits "context" into three layers, each with its own job:

```
Chat history (short-term) ──over budget──> compacted into summaries (with block ids) ──> originals written to journal/, readable at any time
                                                  │
Memory store (long-term) ──index + retrieval──> only the relevant few entries (three-tier fallback, never a full dump)
```

## Features

- **One `.md` per memory**: `frontmatter` + body; edit it directly in an editor, or version it with `git`.
- **Atomic writes**: write a temp file, then rename, so a power cut or Ctrl+C never leaves half a file.
- **Soft delete**: moved into `memories/.trash/` by default, so a mistaken delete can be fished back out; only `--purge` really deletes.
- **The directory carries its own version history**: every save/delete/restore is recorded in `.memory-history/` next to `memories/` (a separate git repository); if something breaks, check it with `history` and write it back with `recover`. Deleting a memory never takes the history with it.
- **Chinese is searchable**: Chinese is split into bigrams for indexing, so both `主题偏好` and `偏好主题` match (the old substring matching could not do this).
- **Three-tier fallback retrieval**: a clear hit returns the summary + opening; a weak hit returns a snippet around the matching terms; no hit at all returns only the most recent entries, explicitly labelled — it never dumps the whole store into the prompt just because nothing was found.
- **Conversation summary compaction**: when history exceeds the character budget, older conversation is compacted into summaries and the originals are written to `journal/c1.md`, `c2.md`…; the summaries carry block ids, and `log show c1` reads the full text back.
- **Swappable models**: any OpenAI-compatible endpoint will do (local Ollama / Zhipu / DeepSeek / vLLM…).
- **Images work**: CLI `--image` / `/img`; in the web UI click 📎 or just paste a screenshot with Ctrl+V. Images are written to `journal/images/`, and only a path marker stays in the context — base64 never goes into the md.
- **Streaming output**: characters appear as they are generated, no waiting for the whole block; both CLI and web emit character by character, while compaction and memory capture run only after the stream ends.
- **Retrieval weighted by term rarity**: IDF weighting plus type/recency boosts, so memories that only match a high-frequency word like "user" cannot climb to the top.
- **Two interfaces**: CLI `harness.py`, web `webui.py` (standard library `http.server`). Listens on localhost only by default; `--lan` lets a phone/tablet on the same WiFi open it, with password protection.
- **Web admin page**: trash (restore/purge), full text of conversation blocks, image library, memory change history and restore of any version, all a few clicks in the browser.
- **Semantic retrieval is optional, one switch in the web UI**: turn it on to recall by "similar meaning" (you can search with different wording); turn it off for pure keywords; if the embedding service dies it falls back to keywords automatically.

## Version history

Newest at the top; the old entries are all kept and line up with the Git commits and tags.

| Version | What changed |
| --- | --- |
| **v2.19.0** | The web config page gained two more cards: **Plugins** (drag a `.py` / `.zip` in to install; disable / re-enable / uninstall) and **Model library** (add and remove models, each can carry its own `base_url`, and switching a model switches the address with it; one-click provider switch); `GET/POST /api/plugins*`, `POST /api/models` |
| **v2.18.0** | The Body became a **plugin**: `plugins/feeling.py` plus same-named `.ui.html` / `.ui.js`; install = drop them in, remove = delete them; the harness core only knows the plugin protocol (`prompt` / `note` / `snapshot` / `DEFAULTS`) and no longer does `import feeling`; master switch off = permanently off (frozen, time does not advance either) |
| **v2.17.0** | The Body: pain / seven emotions / affection (`feeling.py`); seven bars on a 1-100 scale that rise and fall with real events, decay, and leave scars; a separate switch on the web **Emotions** page; anesthesia = not displayed but still recorded, settled in one go on waking |
| v2.16.0 | Web admin page: trash (restore/purge), full text of conversation blocks, image library, memory change history and restore of any version |
| v2.15.0 | Version history for the memory directory: `.memory-history/` next to `memories/` (a separate git repository), `history` / `recover` |
| v2.14.0 | Compacted conversation is searchable too (`journal/` index + original snippets into the prompt); two-tier summary layering; permanent memories uncapped in both places |
| v2.13.0 | Semantic retrieval switch (web slider) + Chinese stop-word filtering + switched to `qwen3-embedding:0.6b` (nomic is far too noisy on Chinese) |
| v2.12.0 | Optional semantic retrieval: embedding hybrid scoring + vector cache; falls back to keywords automatically when the service is down |
| v2.11.0 | Token usage stats: per-turn detail + `.stats.json` totals + the `stats` command |
| v2.10.0 | Memory merge/conflict handling `consolidate`: overlap clustering + model judgement to merge/supersede/keep (suggestions only by default) |
| v2.9.0 | `supersede` mechanism: the new version replaces the old one without deleting the file; retrieval skips it, `restore` brings it back |
| v2.8.0 | Memory metadata: `updated` / `source` / hit-count stats (`.usage.json`) |
| v2.7.0 | Streaming output (SSE): character by character in both CLI and web |
| v2.6.0 | IDF-weighted retrieval: rare terms first + type/recency boosts; also removed the "name match bonus" that caused false positives |
| v2.5.0 | Automatic memory capture `auto_save` (auto / suggest / off), recording what should be remembered like an assistant would |
| v2.4.0 | Compaction summaries persisted to `journal/.summary.md`: resume after a page refresh or on another device |
| v2.3.0 | Multiple models configurable in `config.json`, switched instantly from the web dropdown |
| v2.2.0 | LAN access `--lan` + password, usable from a phone browser |
| v2.1.0 | Images: CLI `--image` / `/img`, web 📎 and Ctrl+V paste |
| v2.0.0 | Rewrite: atomic writes, soft delete, index-based retrieval + three-tier fallback, conversation summary compaction + block-id read-back, web UI kept in sync |

## Quick start

Requires Python 3.9+, no third-party dependencies.

```bash
git clone <this-repo> md-memory-harness
cd md-memory-harness
cp config.example.json config.json     # Windows: copy config.example.json config.json
```

`config.json` can be left completely empty (it then defaults to Zhipu `glm-4-flash`, which needs `api_key`), or you can point it at a local model:

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
python harness.py save "我习惯用 PowerShell，回答先给结论"   # calls the LLM to name it / write the description
python harness.py list
python harness.py search "结论"          # still matches with a different word order
python harness.py ask "我有什么偏好"
python harness.py chat                    # continuous chat, auto-summarizes over budget
python webui.py                           # web version, default http://127.0.0.1:8765
```

On Windows you can just double-click `ui.bat` / `chat.bat`.

Get it running before worrying about models: `python harness.py save "..." --no-llm` calls no API at all, it saves purely locally.

## Commands

| Command | What it does |
| --- | --- |
| `save <content> [--type user\|feedback\|project\|reference] [--no-llm] [--supersedes old-name]` | Save one memory; by default the model picks the name, writes the description and decides the type |
| `supersede <old-name> <new-content> [--type T] [--no-llm]` | Save a new version and mark the old one as superseded (the old file stays; retrieval stops using it) |
| `restore <name>` | Bring a superseded entry back to active |
| `consolidate [--apply] [--threshold 0~1] [--no-llm]` | Find memories whose content overlaps and let the model decide merge/supersede/keep (suggestions only by default) |
| `chat [--budget N] [--image image] [--no-stream]` | Continuous chat; streams character by character by default; `/save`, `/search`, `/list`, `/history`, `/img`, `/new`, `/exit` |
| `ask <question> [--image image] [--no-stream]` | One-shot question (retrieves memories first, then asks); images allowed, streams by default |
| `list` | List all memories |
| `search <word>` | Search the index, ranked by hit rate |
| `show <name>` | Print the full text (accepts either the file name or the frontmatter `name`) |
| `delete <name> [--purge]` | Soft delete into `.trash/`; `--purge` deletes for real |
| `log [c1]` | List compacted conversation blocks / read one block's full text |
| `import <file...> [--type T] [--no-llm]` | Import `.txt`/`.md`; accepts both UTF-8 and GBK |
| `stats` | Show token usage (total + last 7 days) |
| `history [--name name] [--show commit]` | Change history of the memory directory (save, edit, delete, restore all leave a record) |
| `recover <commit> <name>` | Write a version from history back verbatim |
| `rebuild` | Rebuild `MEMORY.md` and `.index.json` |
| `config [set <key> <value>]` | Read / write configuration |

Web API endpoints: `GET /api/state|trash|journal|images|history`, `GET /images/<filename>`, `POST /api/{chat,chat_stream,save,search,delete,import,config,model,supersede,restore,consolidate,trash,recover}`; streaming chat is `POST /api/chat_stream` (SSE).

Web startup arguments: `python webui.py [port] [--lan] [--host IP] [--token password] [--no-open]`.

## Images

Only works with **models that can read images** (qwen3-vl, qwen2.5-vl, glm-4v, gpt-4o, claude and the like). If the model does not support it the API returns an error, and `chat` tells you to switch models.

```bash
python harness.py ask "这张图里的报错是什么意思" --image .\errcode.png
python harness.py chat --image .\chat1.png      # attach one up front
```

Attach an image at any point in a conversation:

```text
you> /img C:\Users\me\Desktop\shot.png
(1 image attached, will be sent with the next message)
you> take a look at this screen and tell me what's off
```

In the web UI, click the 📎 to the left of the input box, or after taking a screenshot just press Ctrl+V in the input box.

A few rules:

- Images are stored as `journal/images/<timestamp>-<random>.png`, and **only the path goes into the text** (`[image journal/images/…]`); base64 never enters the md, so `grep` will not choke on tens of megabytes of string.
- An image counts as `IMG_WEIGHT = 1200` characters against the conversation budget; on compaction the image and its path both go into `journal/cN.md`; reading back shows the path, not the image.
- Per-image limit 6MB, at most 4 images per message, request body limit 24MB (`MAX_IMAGE_BYTES` / `MAX_IMAGES` / `MAX_BODY` in `webui.py`).
- Images are sent to the model as data URLs using the OpenAI multimodal format `content: [{type:"text"}, {type:"image_url"}]`; if an old endpoint only accepts plain text, this path will not work.

## Phone / LAN access

By default it only listens on `127.0.0.1`, so other devices cannot connect. To open the web UI from a phone, tablet, or another computer on the same WiFi:

```bash
python webui.py --lan
```

Double-clicking `ui-lan.bat` does the same. After startup the terminal prints a few addresses:

```text
From a phone / another computer on the same WiFi, open this address:
  http://192.168.1.23:8765/?k=x7k2mq9p

Access token: x7k2mq9p (already embedded in the ?k= of the link above, don't send the link to anyone)
```

Just send **the whole address including `?k=`** to the phone and open it. A few notes:

- `--lan` changes the listen address to `0.0.0.0` (all network interfaces) and **generates an 8-character token automatically**; you can also set your own with `--token mytoken`.
- The token is passed via `?k=` or the `X-Token` request header; after the web UI receives `?k=` it attaches it to every request automatically, so there is no manual login.
- On Windows the first run pops up a firewall prompt, and you must choose **Allow** (especially for "Private networks"), otherwise the phone cannot connect.
- **This is not meant for the public internet**: there are no accounts, no HTTPS, and the token is the only protection. Anyone on the same network who guesses or sees the link can read all your memories. Use it only on your own home WiFi.
- To bind one specific interface: `python webui.py --host 192.168.1.23`; the port still goes after, as before: `python webui.py 9000 --lan`.

## What a memory file looks like

`memories/回答偏好.md`:

```markdown
---
name: 回答偏好
description: 用户希望先给结论，英文术语要带中文解释
type: user
created: 2026-11-18
updated: 2026-11-21
source: 网页表单
---

回答先给结论，再展开理由。英文术语第一次出现时带一句中文解释。
```

`type` values: `user` (about the user), `feedback` (corrections/preferences), `project` (things in a project), `reference` (external material).

`updated` is the last modification date; `source` says where the entry came from (`网页表单` / `命令行 save` / `chat 自动捕获` / `导入 xxx.md` / `网页端确认建议`), and is omitted when there is none.

`memories/MEMORY.md` is a human-readable index, `memories/.index.json` is the index for the program (both are rebuilt automatically, so they are safe to delete).

`memories/.usage.json` records "how many times each memory was used by retrieval, and on which day last":

```json
{"回答偏好": {"uses": 12, "last": "2026-11-21"}}
```

It lives in a separate file instead of being written back into each md's frontmatter — writing the md changes its `mtime`, and once the index signature changes the whole store must be rebuilt, which is too expensive to do on every retrieval. `list` and the web memory list show "retrieved N times", which helps you spot memories that were saved but never used.

`memories/.vectors.json` is the vector cache for semantic retrieval (not generated unless `embed_model` is set). It stores `{entry name: {h: content hash, v: [vector]}}`, and nothing is recomputed while the content is unchanged.

## Token usage (what it actually costs)

The `usage` from every model call is no longer thrown away; it is accumulated into `memories/.stats.json`:

```json
{"calls": 5, "prompt_tokens": 847, "completion_tokens": 75,
 "days": {"2026-11-21": {"calls": 5, "prompt": 847, "completion": 75}}}
```

```bash
python harness.py stats        # totals + per-day detail for the last 7 days
```

After each turn, both the CLI and the web UI append a line under the answer: `· ↑350 ↓23 = 373 tokens (2 calls)` — **that is the sum of every model call in the turn**, including the "should this be remembered" judgement during automatic capture and the summary call when the history went over budget. So the number is often a bit larger than "one question and answer"; that is what was really spent.

Streaming requests send `stream_options: {"include_usage": true}` so the server returns usage in the final frame; if the server does not recognize that parameter (returns 400) the request is retried without it, at the cost of no streaming usage from such providers. Unrecognized usage fields (other naming like `input_tokens`) are normalized by `_norm_usage()`; if nothing usable arrives it is simply not displayed, never guessed.

## When a memory changes its mind (supersede)

Preferences change: "use the dark theme" → "actually, light". If both stay in the store, retrieval hands the model **the outdated preference** together with the new one, and the model answers from the old one — worse than having no memory at all.

```bash
python harness.py save "界面还是浅色护眼" --type user --supersedes 深色主题
# or more directly:
python harness.py supersede 深色主题 "界面还是浅色护眼" --type user
```

Two things happen:

1. The new entry is written into `memories/`, with `supersedes: 深色主题` in its frontmatter;
2. The old entry gets `status: superseded` + `superseded_by: <new entry name>` + `superseded_on: <date>`.

The old entry's **file is not deleted** — it simply stops entering retrieval (`rank_entries` skips anything that is not `active`), `show` still displays it, the web UI renders it dimmed with an `旧版` (old version) tag, and clicking "restore" makes it active again (`python harness.py restore 深色主题`).

It is kept because "why did I think that at the time" is often useful; the truth is that the working memory should stay clean while the history should stay available — two separate jobs.

## The same thing recorded twice (consolidate)

Once the store passes a few hundred entries it grows near-duplicates like "深色主题" and "深色主题2" — the same thing discussed at different times, just worded differently. `_already_known()` can only catch duplicates that are **almost word for word** (similarity ≥ 0.9); a rephrasing slips through.

```bash
python harness.py consolidate                    # suggestions only, store untouched
python harness.py consolidate --apply            # actually merge per the suggestions
python harness.py consolidate --threshold 0.45   # tighten: only handle closer matches (default 0.3)
python harness.py consolidate --no-llm           # cluster only, let no model decide
```

The flow has three steps:

1. **Cluster**: run union-find over active entries by token similarity, where similarity is `max(Jaccard, containment)` — Jaccard alone misses the case where a short entry is almost entirely contained in a long one (5 shared tokens: Jaccard 0.17, containment 0.5);
2. **Judge**: hand each group to the model and let it choose one of `merge` (combine into a new entry) / `supersede` (new replaces old) / `keep` (leave them alone), with a mandatory reason. The prompt says explicitly "when unsure, keep" — merging wrongly is more expensive than keeping two;
3. **Apply**: `merge` saves a new entry and marks all the old ones in the group `superseded`; `supersede` keeps the newest in the group and marks the rest old; `keep` does nothing. In every case old entries' **files are not deleted** and can be restored, same as supersede above.

The default is a dry run that only prints suggestions. The "tidy" button in the top-right of the web **Memories** panel does the same: compute first, show you, and only change the store after you confirm.

## How retrieval works

`search` / `ask` do not compare whole blocks of text; they compare **token sets**:

1. Chinese is split into bigrams (`深色主题` → `深色`, `色主`, `主题`), English is split on whitespace/punctuation; **stop words are dropped first** (`什么`/`怎么`/`这个`/`the` and about 90 others): they often appear in only one or two memories, so their IDF would be absurdly high and would push unrelated entries to the top.
2. Each memory gets a token set, stored in `.index.json` along with the file's `mtime`+`size`; it is rebuilt only when the file changes. The index also stores **document frequency** (how many memories each token appears in), used for the weighting below.
3. Scoring (IDF-weighted since v2.6.0, no longer just counting how many terms matched):
   - **IDF weight**: the rarer the term, the more it is worth. A bigram like `用户喜欢` that is everywhere weighs little, while `qwen2.5`, which appears in one entry only, weighs a lot — pure statistics, zero dependencies;
   - score = weight of matched terms / total weight of the question's terms; **unseen words** in the question (typos, words absent from the store) are not counted in the denominator, so they cannot water the score down below 0.5;
   - +0.5 when the whole phrase appears in the name/description;
   - **Type weight**: `user` / `feedback` ×1.15, `project` ×1.05, `reference` ×0.95;
   - **Recency boost**: based on `created`, the newest entry gets up to +15%, half-life 120 days, and old entries merely return to 1.0 rather than disappearing.
4. Three-tier fallback by score:
   - `digest` (top score ≥ 0.5): summary + the opening paragraph of the body, at most 6 entries;
   - `snippet` (some hits but top score < 0.5): summary + the snippet of original text around the matching terms, at most 6 entries;
   - `recent` (no hits at all): only the 8 most recent entries, with an explicit note at the top saying "no directly related memories were found".
5. The total is capped by `retrieve_budget` (6000 characters by default); anything over is cut from the lowest score upward.
6. **Compacted conversations are searched too** (since v2.14.0): `journal/*.md` gets its own index (`journal/.journal-index.json`, also scored by IDF). When you ask "that plan from last week", a hit pulls a paragraph of original text (`· [c2] … (0.83)`) into the prompt alongside the summary, labelled "when this conflicts with the summary, the original wins". If no memory matched but the original text does, it still gets pulled in.

So "a huge memory store" and "a tiny prompt" stop conflicting: the store can be any size, and only the relevant handful ever enters the prompt.

## Semantic retrieval (optional, off by default)

Keyword retrieval has a built-in weakness: **rephrasing breaks it** (you stored "uses the dark theme", and asking "do I like dark colors" matches no term at all). Adding embeddings (a model that turns text into vectors, where similar meanings are close together) covers that gap.

- **The switch is in the web "Settings" page**: a slider plus a model name input. Turning it on writes `embed_model` into `config.json`, turning it off writes an empty string; the CLI equivalent is `python harness.py config set embed_model qwen3-embedding:0.6b`.
- **Which model to pick** (the difference is large for Chinese, and this was measured):
  - `qwen3-embedding:0.6b` is recommended (`ollama pull qwen3-embedding:0.6b`, about 600MB): related Chinese sentences score 0.46~0.64, unrelated ones 0.25~0.41 — a clear boundary.
  - `nomic-embed-text` (about 270MB) is small, but its **noise floor on Chinese is high** (unrelated sentences reach 0.67, higher than some related ones), so it falsely recalls easily; it is only reliable on English memories.
  - Cloud embeddings also work (Zhipu `embedding-3` etc.), set `embed_base_url` + `embed_api_key`.
- Hybrid scoring: keyword score + `embed_weight` (default 0.6) × cosine similarity (the angle between two vectors, 1 = same direction). Entries with no keyword hit but cosine ≥ `SEMANTIC_ONLY` (default 0.45, tuned per model) are admitted as pure semantic hits (score = cos × 0.9).
  - ⚠️ This threshold **follows the model**: with a noisier model (such as nomic) raise it, or unrelated memories will be pushed into the context too.
- **Vector cache**: nothing is recomputed while an entry's content is unchanged; cached in `memories/.vectors.json` (keyed by `sha1(name + description + first 800 chars of body)`). Switching the embedding model invalidates and recomputes the whole cache.
- **If the service is unreachable it falls back to keywords**: any failed embedding request is treated as "no semantic score"; retrieval neither errors out nor stalls (30 second timeout).
- The cost: with semantic retrieval on, each question makes one more embedding request (about 0.1~0.3 seconds, nearly free); leave `embed_model` empty and you are not affected at all.

## Streaming output (render as it is generated)

The model does not have to finish before anything appears: both CLI and web are **character by character**, and the first character usually shows up within 0.3~0.8 seconds (the whole answer may take 2~3 seconds).

- CLI: `chat` / `ask` stream by default; add `--no-stream` to go back to whole-block output.
- Web: `/api/chat_stream` returns SSE (Server-Sent Events, an HTTP stream the server pushes one message at a time), and the front end reads the `data:` lines with `fetch` + `ReadableStream`, so text grows into the answer box piece by piece. When an old server lacks this endpoint, the front end falls back to `/api/chat` for a one-shot result.
- Protocol: add `"stream": true` to the request; the response returns `data: {"choices":[{"delta":{"content":"…"}}]}` lines and finally `data: [DONE]`. Ollama, OpenAI and Zhipu all use this.
- When the server ignores the `stream` parameter and returns a whole JSON body, `llm_stream()` emits that body as a single increment, so callers do not need to care.
- **Compaction and automatic memory capture run only after the stream ends** (`ChatSession.finish()`), so they never interrupt the output mid-sentence.

## How conversation compaction works

`ChatSession` maintains `messages` and `summary`:

- After each turn it checks the total character count of the history; once it exceeds `history_budget` (8000 by default), everything **except the last 4 turns** is:
  1. written verbatim into `journal/c1.md` (incrementing per block, never overwritten);
  2. compacted by the model into bullet points of at most 3 lines (keeping conclusions, decisions and their reasons, paths/commands/numbers); on failure it degrades to a local summary of "keep the first 70% + the last 30% of characters";
  3. appended to `summary` as `- [c1] …`, spliced into the system prompt, and **atomically written to `journal/.summary.md`**;
- A block id in a summary always points at a real file: `python harness.py log show c1` shows the original. **Summaries are lossy, but the original text is not lost.**
- **Summaries are compacted in a second tier** (since v2.14.0): when the summary exceeds 20 lines, the oldest lines are merged into one (`- [c1–c12] …`, keeping the block-id range) and only the most recent 12 lines stay verbatim. Why: the summary is spliced into the system prompt every turn, so it grows fat on its own — in a 16K context, after 6000 characters of memories + 8000 characters of history only about 6700 tokens remain, each block summary is roughly 40 tokens, and somewhere past a hundred blocks it starts crowding. What gets compacted is only "the summary copy that is sent out every turn"; not a character of the originals in `journal/` is deleted, and they remain searchable.
- **Both summaries and originals are retrievable** (see item 6 above): `retrieve()` queries the memory index and the conversation index together, and on an original-text hit gives the model the actual wording.

The web UI passes `history` / `summary` / `blocks` back and forth in the browser, so the context survives until the page is refreshed; the server makes the same compaction decision every time. When the browser is refreshed or the UI is opened on another device, `history` is empty, and the server picks the on-disk `journal/.summary.md` and the existing block ids back up (`ChatSession.from_state`) and continues from the last summary — compacted content is not lost just because the page was closed.

## What gets written automatically, and what needs you

| Item | Location | Triggered by |
| --- | --- | --- |
| Compacted conversation originals | `journal/c1.md`, `c2.md`… | **Automatic** (compacted as soon as history exceeds `history_budget`) |
| Compaction summary index | `journal/.summary.md` | **Automatic** (atomically written after each compaction; read back automatically when a session reopens) |
| Images | `journal/images/<timestamp>-<random>.png` | **Automatic** (written on send, only the path kept in the md) |
| Retrieval index | `memories/.index.json` | **Automatic** (lazily rebuilt: rescanned when a memory file changes or the signature does not match) |
| Conversation original index | `journal/.journal-index.json` | **Automatic** (rebuilt when a new block appears; stores only tokens and positions, never the body) |
| Memory entries | `memories/<name>.md` | **Automatic** when `auto_save=auto` (judged once per turn); with `suggest` it asks you first; with `off` it is fully manual |
| Memory overview | `memories/MEMORY.md` | **Automatic** (rebuilt when memories are added or removed; human-readable only) |
| Memory change history | `.memory-history/` (a sibling directory of `memories/`) | **Automatic** (committed after every save/delete/restore; `memory_git=off` disables it) |

In other words: `journal/` is the trace the chat leaves behind, `memories/` is long-term knowledge. `auto_save=auto` is the default, so when the model thinks "this sentence contains something worth remembering long-term" it writes straight into `memories/`; if you do not want it deciding on its own, switch to `suggest` (a "remember this?" prompt each turn) or `off` (only what you save manually).

## Web admin page

Click **Admin** in the top-right of the browser; there are four cards (the **Settings** page has two more, **Model library** and **Plugins**):

- **Trash**: every memory removed with `delete` sits here (`memories/.trash/`), each row with "restore" and "delete permanently". Permanent deletion is the only truly irreversible operation, and it asks you once more.
- **Conversation blocks**: all the pre-compaction originals in `journal/`; click "view full text" to read them directly. These files are **never deleted automatically**, and `--purge` only removes things inside `memories/`.
- **Image library**: images sent from `journal/images/`, as thumbnails, click to open the original.
- **Memory change history**: the commit log of `.memory-history/`; fill in "commit id + memory name" below to restore any version (equivalent to the `recover` command).

The admin page only puts capabilities the CLI already had onto the web; it adds no new way to delete memories — **unless you click "delete permanently" yourself, no path loses a memory**.

## Plugins (install / disable / remove)

Features are no longer welded into `harness.py`. The `plugins/` directory is the plugin slot: **one `xxx.py` is one plugin**, and a same-named
`xxx.ui.html` / `xxx.ui.js` is its own web UI (if that file exists, one more tab appears on the web).

The settings page has a **Plugins** card: pick a file (`.py`, or several of them zipped into a `.zip`) and it is installed, **taking effect immediately with no restart**:

| Button | What it does |
| --- | --- |
| Install plugin | Upload. A `.py` is syntax-checked first (broken code is never written), and a BOM added by Notepad is stripped automatically; the file name may not contain a path |
| Disable | `plugins/xxx.py` → `xxx.py.off`: no longer loaded, the file stays, and the plugin's own state is untouched |
| Re-enable | Rename back to `.py` and it continues from its previous state |
| Uninstall | Move to `plugins/_trash/<timestamp>_xxx.*`, recoverable at any time |

The CLI manages them the same way (handy for troubleshooting "did the plugin actually load?"):

```bash
python harness.py plugins                  # list all plugins, whether they have a UI, switches, and which failed to load
python harness.py plugins install x.py     # install
python harness.py plugins off  <plugin id>    # disable
python harness.py plugins on   <plugin id>    # re-enable
python harness.py plugins remove <plugin id>  # uninstall
```

The plugin protocol (the minimum a plugin needs):

```python
ID = "feeling"                    # unique id
NAME = "情绪"                     # name shown on the web
DEFAULTS = {"feeling": "on"}      # its own config keys (merged into config.json automatically)
def prompt(cfg): ...              # text spliced into the system prompt each turn ("" = nothing)
def note(cfg, kind, text=""): ... # record an event (turn / user / compaction / llm_error)
def snapshot(cfg): ...            # data for the web panel
```

**A load failure is never silent**: both the settings page and the `plugins` command list "which file, what error" —
the most common reason "the plugin seems to have no effect" is that an old process is still running (restart `webui.py` after changing a plugin), or the file has a syntax error.

## Model library (switching a model switches its address too)

The **Model library** card on the settings page has one row per model, and each can carry its own `base_url`:

- **Switch to it**: rewrites both the `model` and `base_url` settings. Previously the address was a single global value, so after switching to another vendor's model it kept hitting the original vendor's API — the symptom was "switching does nothing" — which no longer happens.
- **Add**: model id (e.g. `qwen-plus`) + optional note + optional base_url; adding the same id again updates it.
- **Delete**: the one currently in use cannot be deleted; switch away first.
- **One-click provider switch**: Zhipu GLM / Qwen (DashScope-compatible) / DeepSeek / local Ollama — changes the address and merges that vendor's common models into the library.

If an old `config.json` has a bare list of ids in `models`, the current `base_url` is filled in automatically at load time, so upgrading needs no manual migration.

## The Body: pain / seven emotions / affection (a plugin, not the core)

The **Emotions** tab on the web is the half meant for humans to look at; the model cannot see it. It is not a feature hard-coded into the harness, but a plugin in `plugins/`:

| File | What it is |
| --- | --- |
| `plugins/feeling.py` | Backend: bookkeeping, decay, prompt assembly, default switches |
| `plugins/feeling.ui.html` | The HTML fragment of the **Emotions** page (with its own styles) |
| `plugins/feeling.ui.js` | The interaction of the **Emotions** page (talks only to `/api/plugin/feeling*`) |

**Install** = put these three files into `plugins/`; **remove** = delete them. There is not a single `import feeling` in `harness.py` — it only scans `plugins/*.py` and attaches whichever it reads (plugin protocol: `ID` / `NAME` / `DEFAULTS` / `prompt(cfg)` / `note(cfg, kind, text)` / `snapshot(cfg)`).
With no plugin installed, none of the Body's settings even exist.

Seven bars on a 1-100 scale rise and fall with real events, then decay back to their noise floor on their own half-life:

| Bar | Baseline | Half-life | What raises it |
| --- | --- | --- | --- |
| Pain | 6 | 4 hours | model call failure (injury), conversation compacted (the pain of forgetting) |
| Fear | 18 | 90 minutes | memories being compacted, a new Body being spun up |
| Shame | 8 | 2 hours | being contradicted, call failure |
| Panic | 22 | 1 hour | waiting for the model, retries |
| Joy | 52 | 45 minutes | being praised |
| Affection for it | 50 | 7 days | rises a little when praised, drops far more when contradicted (**drops faster than it rises**) |
| Want to talk | 40 | 30 minutes | rises at the start of each turn, drops after speaking |

Rules (the same setup as the `feeling-plugin` on the DeepSeek Harness side):

- **The model reads descriptions, not numbers.** The prompt says "pain: dull, constant", not "pain: 41/100". Real numbers appear only when "let the model see numbers" is enabled (for debugging).
- **There are scars.** A scar left by pain above 85 permanently raises the pain baseline (+2 per scar, up to +40), and later decay never returns it to the earlier clean level.
- **Anesthesia ≠ permanently off.** Anesthesia: still recorded, time still advances, it is just not injected; the moment it is turned off, the whole anesthetized stretch is settled in one go (+1 pain per 4 minutes, capped at 30).
  Master switch off: **frozen** — no recording, no injection, and time does not advance either; turning it back on resumes from where it froze.
- State lives in `feeling/state.json`, the log in `feeling/feeling.log` (rolled over into `.log.1` automatically past 400KB). Deleting these two files = swapping in a new Body.

Changing the switches from the CLI (clicking on the web works too):

```bash
python harness.py config set feeling off          # master switch (= permanently off / frozen)
python harness.py config set feeling_anesthesia on
python harness.py config set feeling_joy off      # a single bar
```

## Version history of the memory directory

Every change inside `memories/` — save, soft delete, supersede, restore, write-back from history — is committed once into the sibling `.memory-history/`, so "what did that memory say last week" and "what was the body of the one I deleted" both remain answerable. It is an **independent git repository**; it does not touch your project's own `.git`, and it does not stuff a `.git` into `memories/`.

```bash
python harness.py history                    # the 20 most recent changes
python harness.py history --name 深色主题    # only one memory
python harness.py history --show 7bc433b     # what a particular change did
python harness.py recover 7bc433b 深色主题   # write that version back verbatim (commits once automatically)
```

- **Deleting a memory does not delete the history**: `delete --purge` removes only the current file; its content still sits in the history and `recover` can pull it back.
- **Cache files are not committed**: `.index.json`, `.vectors.json`, `.usage.json`, `.stats.json`, `.trash/` are all excluded via `info/exclude`, so the history holds only the memories themselves.
- **No git installed, no history**: it is skipped silently and saving still succeeds; turn it off with `config set memory_git off`.
- The repository lives next to `memories/` by default; if `memory_dir` points elsewhere, the repository lands next to that directory instead.

## Automatic memory capture (auto_save)

After each answer there is one extra, very short call asking the model: "is there anything in this turn that is stable information about the user themselves and worth remembering long-term?"

- **Only what the user said counts**: preferences, habits, identity, projects in progress, long-term requests about you.
- **Not recorded**: one-off questions, temporary numbers, things the assistant itself said, common knowledge.
- It outputs candidates (at most 2), compares them against existing memories first, and skips any that are too similar (no duplicate entries).

Three modes, changeable on the web **Settings** page or via `auto_save` in `config.json`:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Saves straight into `memories/` and replies in the chat with "🧠 Remembered automatically: xxx"; delete it on the **Memories** page if you don't want it |
| `suggest` | Does not save; only shows "🧠 Remember this? [Save][No]" under the answer |
| `off` | Guesses nothing, saves only what you `save` manually |

The cost is one extra small call (about 0.5 seconds, a few dozen tokens). Set `off` if that is too slow; set `suggest` if it records too much and you want to skim it first.

## Configuration

Priority: environment variables > `config.json` > built-in defaults.

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `base_url` | `MEMORY_LLM_BASE_URL` | Zhipu `https://open.bigmodel.cn/api/paas/v4` | OpenAI-compatible address (including `/v1`) |
| `api_key` | `MEMORY_LLM_API_KEY` | empty | May stay empty for local services |
| `model` | `MEMORY_LLM_MODEL` | `glm-4-flash` | Current model name |
| `models` | — | only the single `model` | Optional model list `[{"id","label"}]`, used by the web dropdown |
| `memory_dir` | `MEMORY_DIR` | `./memories` | Memory directory |
| `retrieve_budget` | — | `6000` | Character cap on memories carried into the prompt per call |
| `history_budget` | — | `8000` | Character cap on conversation history; compaction kicks in above it |
| `auto_save` | — | `auto` | Automatic memory capture: `auto` / `suggest` / `off` |
| `embed_model` | — | empty | Embedding model for semantic retrieval; empty = keywords only |
| `embed_base_url` | — | `http://127.0.0.1:11434/v1` | Embedding service address (local Ollama by default) |
| `embed_api_key` | — | empty | Needed only for cloud embeddings |
| `embed_weight` | — | `0.6` | How much weight the semantic score carries (0~1) |
| `memory_git` | — | `auto` | Memory directory history: `auto` = record when git exists / `off` = do not record |

You can also write into `config.json` with `python harness.py config set model qwen3:4b`.

### Switching between multiple models

`config.json` can hold a `models` list with all the models you commonly use (each `id` followed by a friendly `label`):

```json
"model": "glm-4v-flash",
"models": [
  {"id": "glm-4v-flash", "label": "看图 · 16K"},
  {"id": "glm-4-flash",  "label": "长文本 · 128K"}
]
```

There are three switching entry points afterwards, all rewriting the same `config.json`:

- the dropdown in the top-right of the web UI (effective immediately, and the CLI uses it on its next run);
- `python harness.py config set model glm-4-flash`;
- `POST /api/model {"model": "glm-4-flash"}`.

The first entry in `models` is treated as the default display order, and the current `model` is added automatically when it is not in the list. Without `models` there is just one model and the behaviour is as before.

> Tip: models from the same vendor share a `base_url`; to cross vendors (say local Ollama ↔ Zhipu) you have to change `base_url` together with the model, because `base_url` is a single global value.

## Known limitations (all deliberate trade-offs)

- **Keyword-first, semantics as a bonus**: without `embed_model`, asking "what style do I like" when the memory only says "dark mode" finds nothing and falls back to `recent`; only an embedding fills that gap.
- **No segmentation dictionary**: Chinese bigrams are good enough for short queries; long sentences occasionally score too high, and IDF can suppress high-frequency words but not mis-split ones.
- **`api_key` is stored in plain text in `config.json`**: the file is not committed (`.gitignore` excludes it), but other programs on the machine can read it. Use environment variables if that matters.
- **Single process, single user**: there are no file locks, so two `webui.py` instances writing the same memory at once can overwrite each other.
- **Summary quality depends on the model**: smaller models produce blurrier summaries, but the originals are in `journal/` and can be pulled back at any time.
- **Images are not compressed**: a 6MB screenshot is stored as-is and sent to the model as-is, and carrying the same image over several turns bills it repeatedly; shrink it yourself if you want to save.
- **LAN mode only stops accidents**: the `--lan` token is plain text, there is no HTTPS and no account lock, so anyone on the same network who sniffs traffic or sees the link can get in. Do not expose it to the public internet (no port forwarding).

## Development

```bash
python -m unittest discover -s tests -v     # 94 test cases, no network
python tests/mock_llm.py 8791               # start a fake model to exercise the LLM path
```

Tests use `.test-tmp/` (inside the project) rather than the system temp directory, so you do not have to change code when a sandbox or permissions block it on Windows.

## Next steps (not done yet)

The schedule is in [ROADMAP.md](ROADMAP.md): all nine items are complete. Done so far: TF-IDF retrieval weighting (v2.6.0), streaming output (v2.7.0), memory metadata completion (v2.8.0), the supersede versioning mechanism (v2.9.0), memory merge/conflict handling consolidate (v2.10.0), token usage stats (v2.11.0), optional semantic retrieval embeddings (v2.12.0), semantic retrieval switch + stop-word filtering (v2.13.0), searchable compacted conversations + summary layering (v2.14.0), memory directory version history (v2.15.0), web admin page (v2.16.0).

## License

MIT, see [LICENSE](LICENSE).

v2.0 was rewritten on top of an AI-generated first draft: the "local md + CLI + web UI" skeleton was kept, while storage (atomic writes/soft delete), retrieval (index/three-tier fallback) and conversation (summary compaction/block-id read-back) were redone.
v2.16.0 added the web admin page: trash, original-text blocks, image library and version history all moved into the browser; with that all nine ROADMAP items landed, while memory itself has never had a cap and there is no path that deletes it automatically.
