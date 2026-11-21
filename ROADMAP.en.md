# Roadmap of Pending Items (specified by the user on 2026-11-20)

[简体中文](ROADMAP.md) | English

> Status: **unfinished items are recorded only, no code has been touched**. Shipped: #3 (v2.6.0), #6 (v2.7.0), #5 (v2.8.0), #2 (v2.9.0), #1 (v2.10.0), #9 (v2.11.0), #4 (v2.12.0), #7 (v2.15.0); see "Completed" at the end of this document.
> All nine items were raised by the user (#1–#4 and #5–#9 were all handed over at once on 2026-11-20); below is the original intent plus the key implementation points. For the dependency order, see the end of the document.

## 1. Memory consolidation / conflict handling (consolidate) — ✅ Completed (v2.10.0, 2026-11-21)

Right now there is only write-time deduplication via `_already_known()` (it blocks only when the top retrieval score ≥ 0.9); once the library grows, these are bound to appear:

- the same fact stored twice in different wording
- "the user likes dark" and, three months later, "the user switched to light" coexisting, so retrieval returns the old and the new together

Approach: add a `harness.py consolidate` subcommand

1. Use the existing index (the tokens in `memories/.index.json`) to cluster similar entries
2. Let the LLM judge each cluster: **merge / new supersedes old / keep both**
3. **Give suggestions first, execute only after human confirmation** (never modify the library automatically)

## 2. supersede mechanism (versioning for memories) — ✅ Completed (v2.9.0, 2026-11-21)

- frontmatter gains `status: active | superseded`, plus `supersedes` / `superseded_by` / `superseded_on`
- the shipped commands are not the originally planned `update`, but `save --supersedes <old name>` and `supersede <old name> <new content>` (plus `restore <name>` to undo)
- the retrieval layer `rank_entries()` skips entries that are not `active`, so `retrieve()` naturally cannot bring them back either; the files are kept, `show` can look them up and the web side can restore them
- old entries without a `status` field fall back to `active` (`status_of()`); `.index.json` goes up to `INDEX_SCHEMA = 4`, and entries carry `status` / `superseded_by`
- a typo in the old name only warns, never fails: the new entry is written first and the `FileNotFoundError` is swallowed — saving one memory should not be lost entirely because an argument was mistyped

Effect: this is the foundation for #1; it also incidentally fixes "the preference changed but the old memory still haunts retrieval".

## 3. Retrieval weighting (going TF-IDF) — ✅ Completed (v2.6.0, 2026-11-21)

~~Right now `rank_entries()` is pure hit rate: a bigram that appears everywhere like "the user likes" and a distinctive word like "qwen2.5" get **the same weight**.~~

- Add IDF weights (the index already stores all tokens, so counting document frequency is enough — **zero dependencies**)
- A slight recency boost (`created` field already exists)
- Weight the `user` / `feedback` types

Assessment: the smallest change with the most direct retrieval improvement; recommended to do it first.

## 4. Semantic retrieval (local embeddings) — ✅ Completed (v2.12.0, 2026-11-21)

Bigram matching is literal: "likes dark colors" cannot find "dark theme preference".

- Ollama ships an embeddings endpoint of its own (`nomic-embed-text` / `bge-m3`, local and free)
- store vectors in `.index.json`, mixing cosine similarity with the keyword score
- **compute cosine in pure Python, install nothing**, keeping the zero-dependency baseline

Note: the `.index.json` structure will change → add a `schema` version number and rebuild old indexes directly; `config.json` gains `embed_model`, and when it is unset the code falls back to keyword retrieval.

## 5. Memory metadata completion — ✅ Completed (v2.8.0, 2026-11-21)

Two more frontmatter fields:

- `updated`: last-modified time
- `source`: how this entry came to be (`web form` / `command-line save` / `chat auto-capture` / `import xxx.md` / `web-side confirmed suggestion`)

`last_used` is not written into frontmatter; it moved to `memories/.usage.json` (each entry `{"uses": N, "last": "YYYY-MM-DD"}`) — the original plan's "batch up and then write to disk" existed because writing md changes `mtime` and triggers a full library rebuild, so it is better not to write md at all.

Its role is the data foundation for #1 (merge judging) and for time weighting; it also makes "which memories have never been used" visible — sorting by `uses` tells you which are dead entries (both `list` and the web side show it).

## 6. Streaming output (SSE) — ✅ Completed (v2.7.0, 2026-11-21)

~~Right now chat waits for the model to **finish the whole paragraph before showing anything**; the local 7b model takes several seconds, which feels like a freeze.~~

- both Ollama and the OpenAI protocol support SSE (Server-Sent Events, the server pushes item by item)
- both CLI and web switch to character-by-character output
- the web side uses `fetch` + `ReadableStream` to read the `data:` lines (not EventSource, because POST is required)
- mind the coordination with compaction/auto-capture: run `compact_if_needed()` and `capture_memory()` after the stream ends, never in the middle of it

Assessment: purely experiential; the change is concentrated in the forwarding in `llm()` and `webui.py`, low risk and quick to show.

## 7. Putting the memories directory under git — ✅ Completed (v2.15.0, 2026-11-21)

The direction changed during implementation: **do not** stuff a `.git` into `memories/`; instead put `.memory-history/` next to it and operate with
`git --git-dir=.memory-history --work-tree=memories`. Reason: `memories/` is quite likely already inside some project repository, and a nested `.git` would make the outer repository treat it as a gitlink, which is both hard to explain and easy to stumble on.

- save, soft delete, hard delete, supersede, restore, write back from history — one commit each; `memory_git=off` disables it, and without git installed it silently skips
- cache files (`.index.json`/`.vectors.json`/`.usage.json`/`.stats.json`/`.trash/`) are written into `info/exclude`, so history holds only the memories themselves
- new `history [--name] [--show]` and `recover <commit> <name>`: even deleted files can be fished back out of history
- 5 offline test cases (`TestMemoryGit`), requiring git on the machine, skipped when absent
- pitfalls hit: when `MEM_DIR` contains `..`, git reports `Invalid path`, so `normpath` is mandatory; `subprocess` with `text=True` decodes Chinese commit messages as GBK, so `encoding="utf-8"` must be explicit; a failed initialization leaves an empty shell directory containing only `objects/`, so "is this a repo" must be judged by `HEAD` rather than by the directory's existence

After `save` / `delete` / `consolidate` it automatically runs `git commit` (committing only `memories/`).

- the evolution history of memories is fully auditable and can be rolled back
- it can naturally sync to other machines (add a `memory pull/push` and it becomes multi-machine sync)
- it is a perfect match for the positioning "memory library = local md"

Note: the user may put `memories/` inside an existing repository → probe with `git rev-parse --show-toplevel` first; if it is a subdirectory, commit only that subpath; when there is no repository, optionally `git init` or simply skip (config switch `git_autocommit`).

## 8. Web admin surface — ✅ Completed (v2.16.0, 2026-11-21)

Right now the web side can only chat, view the memory list and edit config; three pieces are missing:

- **Recycle bin**: browse `memories/.trash/`, supporting restore (`restore`) and permanent deletion
- **journal raw-text blocks**: clicking `[c1]` in a summary shows that block's raw text directly (currently only the command-line `log show c1` can)
- **Image library**: browse `journal/images/` and see the images sent in the past

## 9. token usage statistics — ✅ Completed (v2.11.0, 2026-11-21)

The `usage` field in LLM responses is currently thrown away.

- each call records `prompt_tokens` / `completion_tokens`, accumulated into `memories/.stats.json` (the original plan said `journal/.usage.json`; it landed next to the memory directory, keeping `.usage.json` company)
- the web top bar shows the accumulated total, and below an answer it shows "how much this round cost"
- with models that have a free quota like `glm-4-flash`, you then know where you stand

## Suggested order

Ordered as "quick wins first, then foundations, then structural changes":

1. **#3 TF-IDF weighting** — independent, effective the same day, retrieval gets accurate immediately
2. **#6 streaming output** — independent, purely experiential, the user feels it every day
3. **#5 metadata completion** — the data foundation for #1/#3, the earlier the better
4. **#2 supersede** — the foundation of #1
5. **#1 consolidate** — depends on #2 and #5
6. **#9 token usage** — small, do it in passing
7. **#4 embedding** — independent, but it changes the index structure
8. **#7 memories under git** — a data-layer change, put it later to be safe
9. **#8 web admin surface** — pure interface, fill it in last

## Common requirements

- keep everything **pure standard library**, no third-party dependencies
- add offline unit tests for every item (45 now), and keep README in sync
- after changes, as usual: local tests → commit → push with `.scratch\gh_push_single.py` → check CI turns green
- user preference: conclusion first, reasons after; English terms should come with a Chinese explanation

## Completed

### v2.16.0 (2026-11-21) — #8 web admin surface

- `harness.py`: `list_trash()` / `restore_trash()` / `purge_trash()`, `journal_blocks()` / `journal_text()`, `list_images()`.
- `webui.py`: GET `/api/trash`, `/api/journal[?block=c1]`, `/api/images`, `/images/<file>`, `/api/history`; POST `/api/trash` (restore/purge), `/api/recover`.
- `ui.html`: a fourth tab "Manage", with four cards (recycle bin / raw-text blocks / image library / version history).
- `memgit_commit()` commits a "first record" when creating the repo for the first time, so history never starts halfway.
- tests: 4 cases in `TestManage` (a soft-deleted file can be recovered and the index rebuilt, only purge is a real delete, path traversal is rejected, `journal_text` accepts only `cN`), **94 green in total**.
- hard constraint: the admin page only moves existing capabilities onto the web, **there is no automatic deletion path whatsoever**; permanent deletion requires two manual confirmations.


### v2.15.0 (2026-11-21) — #7 memory directory version history

- the user's premise is unchanged: "permanent memory must not be touched unless removed by hand" — so version history is **adding protection**, not adding cleanup: deleting a memory deletes only the current file, that version still exists in history, and `recover` can write it back
- an independent git repository `.memory-history/` beside `memories/`, operated with `git --git-dir ... --work-tree memories`, not nested and not affecting the project repository
- auto-commit occasions: `save_memory` / `delete_memory` (soft delete and purge commit separately) / `supersede_memory` / `restore_memory` / `memgit_restore_file`
- CLI: `history`, `history --name`, `history --show <sha>`, `recover <sha> <name>`; config item `memory_git=auto|off`
- 94 cases all green (5 new in `TestMemoryGit`, auto-skipped when git is absent)

### v2.14.0 (2026-11-21) — compacted conversations become retrievable + summary tiering

- the user decided: "permanent memory must not be touched, that is this project's original intent; the rest you may optimize as you see fit" → memory entries and `journal/` raw text get **no cap whatsoever**; only "the part stuffed into the prompt each round" is managed
- `journal/` gets an index of its own (`journal/.journal-index.json`, signed by `filename:size`, rebuilt only when it changes); `rank_journal()` uses the same IDF scoring (threshold 0.35, higher than for memories); `_journal_snippet()` extracts the line with the most hits (within 160 characters)
- the `retrieve()` return value gains `journal` / `blocks`: besides memory hits it carries 1~2 raw-text snippets; when the memory library is completely empty it goes straight to `tier="journal"`
- `prepare()` marks raw-text snippets as "the original words before compaction; when they conflict with a summary, this is authoritative"
- second-level summary compression: `SUMMARY_MAX_LINES=20` / `SUMMARY_KEEP_LINES=12` / `SUMMARY_CAP=6000`; `tier_summary()` merges the oldest batch into `- [c1–c12] …` (block-number ranges preserved) and deletes not a single original
- why: in a 16K context, after 6000 characters of memory + 8000 characters of history only about 6700 tokens remain; a block summary is about 40 tokens, and after a hundred-odd blocks every round starts to squeeze — what gets compressed is only "that draft to be sent out each round"
- 8 new cases (`TestJournalRecall` 4, `TestSummaryTiering` 4), **85 green in total**
- `print_search()` and `/api/search` also carry raw-text hits (`journal_text` / `journal_blocks`)

### v2.11.0 (2026-11-21) — #9 token usage statistics

- `llm()` no longer throws away the `usage` it receives: `record_usage()` normalizes and accumulates it into `memories/.stats.json` (total + per day); `load_stats()` / `stats_totals()` read it back
- `_norm_usage()` recognizes both naming schemes `prompt_tokens`/`completion_tokens` and `input_tokens`/`output_tokens`; when it cannot tell, it returns `{}` (no wild guessing)
- streaming requests carry `stream_options: {"include_usage": true}`; if the server returns 400 it automatically retries without it — Zhipu was measured to accept this parameter, with the last frame carrying the full usage
- `ChatSession.ask()` / `ask_stream()` compute this round's cost from **the total difference before and after the call** (`_usage_delta()`), so it covers the answer, the auto-capture judging, the over-budget summary and all other calls; the result goes into `r["usage"]` and `self.last_usage`
- after each answer the command line prints `· ↑350 ↓23 = 373 tokens (2 calls)`; a new `stats` subcommand shows the accumulated total and the last 7 days
- web: the `/api/chat` and `/api/chat_stream` responses carry `usage`, with an extra line below the answer; the header gains a `token` badge (hover for the per-day breakdown); `/api/state` carries `stats`
- tests add 5 `TestUsageStats` cases (normalizing both field sets, accumulation and per-day, `.stats.json` not treated as a memory, `ask` reporting this round's usage, stats readable), **71 green in total**
- measured with Zhipu `glm-4v-flash`: command line `↑147 ↓29 = 176 tokens`; one web streaming round `↑350 ↓23 = 373 tokens (2 calls)`

### v2.13.0 (2026-11-21) — semantic retrieval switch + stopword filtering

- the settings page replaces the "semantic retrieval embed_model" input box with a **switch (CSS slider) + status text + model name**: `setEmbed(on)` calls `/api/config` to write `embed_model`, writing the model name when on and an empty string when off; `renderAll()` fills back `checked` and "on · model name" / "off (keywords only)"
- new `STOPWORDS` (about 90 Chinese bigrams + common English words: 什么/怎么/这个/一下/可以/我们/但是/如果/已经/没有/不是/就是 the/and/for/what/how…), with both Chinese and English filtered in `tokenize()`
  - the cause was an odd result on real data: asking "what model is installed on the work computer", "writing style" got pushed to first place — Chinese stopword bigrams appear only once or twice in the library, `df=1` → extremely high IDF → an entirely unrelated memory scored full marks
  - after the fix, the same question: "work environment" 1.48 first
- `SEMANTIC_ONLY` 0.55 → **0.45**, and written into the README: this threshold **follows the model**, it is not a universal constant
- measured the noise floor of two models (4 documents × 8 query sets):

  | Model | Dimensions | Related-sentence cos | Unrelated-sentence cos | Conclusion |
  |---|---|---|---|---|
  | `nomic-embed-text` (270MB) | 768 | 0.30~0.73, mean 0.53 | 0.36~0.67, mean 0.56 | Chinese **cannot be separated**: unrelated sentences score higher than some related ones |
  | `qwen3-embedding:0.6b` (600MB) | 1024 | 0.46~0.64, mean 0.57 | 0.25~0.41, mean 0.31 | clear boundary, the 0.45 threshold is clean |

  - conclusion: **use `qwen3-embedding:0.6b` for Chinese**; both `EMBED_DEFAULT` and the README switch to it; nomic is only stable on English memories
  - nomic with a 0.62 threshold still produces false recalls ("what model runs on my computer" → "answer preferences" 0.67); after switching to qwen3 the same question no longer mis-recalls
- tests add `test_stopwords_do_not_take_part_in_scoring` (stopwords filtered out, real words kept, ordering correct), **77 green in total**
- real-model acceptance (a temporary library with two memories): semantic on → "a bit darker is comfortable to look at" recalls "interface theme" 0.65; semantic off → not a single hit

### v2.12.0 (2026-11-21) — #4 optional semantic retrieval (embedding)

- config gains `embed_model` / `embed_base_url` (default: local Ollama) / `embed_api_key` / `embed_weight` (default 0.6); **when `embed_model` is empty the behavior is exactly identical to v2.11.0**
- `embed_text(text)` goes through the OpenAI-compatible `POST /embeddings`, accepting both `data[0].embedding` and Ollama's native `embedding`; any failure returns `None` (retrieval falls back to keywords, no error)
- `_cosine(a, b)` is pure Python; `.index.json` goes up to `INDEX_SCHEMA = 5`, each entry may carry `vec`, and `embed_model` is recorded (switching models recomputes the whole library)
- vector cache `memories/.vectors.json`: decides whether content changed by `sha1(name+description+first 800 characters of body)`; unchanged vectors are reused directly, so there is no repeated spending
- `rank_entries()` becomes hybrid scoring: on a hit, `keyword score + embed_weight × max(cos, 0)`; entries with no keyword hit at all but `cos ≥ SEMANTIC_ONLY = 0.55` are admitted as pure semantic hits (`cos × 0.9`)
- web: `api_state()` carries `embed_model`, and the settings page gains a "semantic retrieval embed_model" input box
- tests add 5 `TestSemantic` cases (semantic recall without any shared word, fallback to keywords when embedding is down, the vector cache is not recomputed, `.vectors.json` not treated as a memory, the index records the model name), **76 green in total** — using a local fake `/embeddings` service that returns deterministic 3-dimensional vectors, no network and no real model download
- real-world constraint: the local Ollama has no embedding model installed (`ollama pull nomic-embed-text` would download several hundred MB), so this feature stays off by default until the user is willing to download a model

### v2.10.0 (2026-11-21) — #1 memory consolidation / conflict handling (consolidate)

- `similar_groups(threshold=0.3, max_groups=8, max_size=4)`: uses only active entries, doing union-find clustering by token similarity
- similarity is `_similarity() = max(Jaccard, containment)` — **Jaccard alone would miss**: measured on two "dark theme" memories with 5 words of intersection, Jaccard was only 0.25 (<0.3, no clustering), while containment was 0.45; "the short one is almost contained in the long one" is exactly the typical shape of a semi-duplicate entry
- `judge_group()`: throws each group at the model to choose one of `merge` / `supersede` / `keep`, with the prompt requiring "when unsure, keep"; when the model returns non-JSON it degrades to `keep` (reason written as "cannot judge"), so the tidying flow never crashes
- `_apply_group()`: merge creates a new entry and marks all the old ones in the group as superseded; supersede keeps the newest one in the group and lets it replace the rest; if the model forgot the body, the `- body` lines are concatenated; if it forgot the name, the newest one is kept; keep changes nothing
- CLI `consolidate [--apply] [--threshold 0~1] [--no-llm]`, dry-run by default printing only suggestions; the web "memories" panel gains a "tidy" button (dry-run first, then a confirm dialog, then apply)
- tests add 5 `TestConsolidate` cases (only overlapping ones cluster, merge creates a new one and demotes the old ones, the newest is kept when the model forgets the name, keep leaves the library untouched, bad JSON does not crash), **66 green in total**
- measured: two "dark theme" entries in a temporary library → the model judges merge → a new entry "the interface uses a dark theme", both old ones become `⤳ superseded by …`, all files still there; the web `/api/consolidate` judged three entries (including one auto-captured) as one group in one go

### v2.9.0 (2026-11-21) — #2 supersede mechanism

- `save_memory(..., supersedes="")`: the new entry's frontmatter records `supersedes`, and it calls `supersede_memory()` to demote the old entry
- `supersede_memory(old, by)` / `restore_memory(name)` / `status_of(meta)` / `dump_memory(meta, body)` / `_set_meta(name, updates)` (a value of `None` means delete the key)
- `rank_entries()` skips `status != active`; `.index.json` goes up to `INDEX_SCHEMA = 4`, each entry carrying `status` / `superseded_by`
- the command line gains `supersede <old name> <new content>` and `restore <name>`, `save` gains `--supersedes`; `list` marks them with `⤳ superseded by X`
- web: `/api/supersede`, `/api/restore`; in the memory list old versions are dimmed with an `old version` tag and a "restore" button
- tests add 6 `TestSupersede` cases (writing supersedes, superseded ones excluded from retrieval, restore revives, index schema 4 carries status, a mistyped old name does not lose the new entry, the web supersede/restore closed loop), **61 green in total**

### v2.8.0 (2026-11-21) — #5 metadata completion

- `save_memory(...)` takes one more `source` parameter; frontmatter writes `updated` (that day) and `source` (only when present); `do_save` / `import_text` / auto-capture / the web side all pass the source per scenario
- new `usage_path()` / `load_usage()` / `touch_usage()`: hit statistics go to `memories/.usage.json` (`{"uses": N, "last": "YYYY-MM-DD"}`), **never written back to md** — writing md changes `mtime`, and once the index signature changes the whole library rebuilds
- `retrieve()` calls `touch_usage(names)` after a hit; `list` and the web memory list show "retrieved N times / never retrieved"
- tests add 5 `TestMeta` cases (`updated` persisted, `source` optional, `touch_usage` accumulates, `retrieve` records one automatically, writing statistics does not trigger an index rebuild), **55 green in total**

### v2.7.0 (2026-11-21) — #6 streaming output

- `harness.py` gains `llm_stream(messages, temperature)`: the request carries `"stream": true` and parses `choices[0].delta.content` from each `data:` line; when the other side ignores stream and returns one whole JSON, the whole thing counts as a single output (this path is taken when `Content-Type` is not event-stream)
- `ChatSession` is split into `prepare()` (retrieval + prompt assembly) / `finish()` (append to history + compaction + memory recording) / `ask()` / `ask_stream()`; the streaming version puts the final result in `self.last_result`
- the command line `chat` / `ask` output character by character by default; add `--no-stream` to go back to whole-paragraph display; when streaming fails, `ask` automatically falls back to whole-paragraph `llm()`
- `webui.py` gains `POST /api/chat_stream` (SSE: `data: {"delta":…}` … `data: {"done":true,…state}`), with request-body reading extracted into `_read_json()` for reuse in `do_POST`; there is no `Content-Length`, and the end of the stream is signalled by closing the connection
- `ui.html` gains `streamChat()` (`fetch` + `ReadableStream` + `TextDecoder` manually assembling frames on `\n\n`), automatically falling back to `/api/chat` when the server lacks that endpoint
- tests add 5 `TestStreaming` cases: really start a fake SSE service to verify parsing, JSON fallback when stream is ignored, `ask_stream` appends to history at the end, streaming also triggers compaction, really start the `webui` service to verify the `/api/chat_stream` event structure. **50 green in total** at the time
- measured with Zhipu `glm-4v-flash`: first increment at 0.43s, whole paragraph 2.37s (47 increments); the command line is character-by-character the same

### v2.6.0 (2026-11-21) — #3 retrieval weighting

- `.index.json` goes up to `INDEX_SCHEMA = 3`, gaining `n` (document count) and `df` (document frequency table); old-version indexes rebuild automatically
- `_idf(idx)`: `log(1 + (n+0.5)/(df+0.5))`, pure statistics, zero dependencies
- `rank_entries()` becomes IDF-weighted: score = weight of the hit words / total weight of the question words; unknown words do not enter the denominator; a whole-sentence hit on the name/description +0.5; then multiplied by the type weight and `_recency_boost()` (half-life 120 days, at most +15%)
- **removed "extra weighting for name hits"**: name/description tokens are already counted into the document, and weighting them again would let entries that only hit high-frequency words like "user" outrank entries that hit distinctive words (caught by a new test)
- 6 new cases (rare words first, unknown words do not dilute, recency breaks ties, type weights, old index rebuild, df counted per document), 45 green in total
