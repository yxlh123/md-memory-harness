#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md-memory-harness — 本地 Markdown 记忆 + 分层检索 + 对话摘要压缩。

三层结构：
  1) 存储层：一条记忆一个 .md（frontmatter + 正文）。
     - 原子写（先写临时文件再改名），不会因中断留下半个文件；
     - 软删除（移到 .trash/），删错还能捞回来；
     - 定位条目时同时认「文件名」和「frontmatter 里的 name」。
  2) 检索层：中文按二元组（bigram）切分建索引，按命中率排序，三级回退：
       digest  —— 命中明确：只给摘要 + 开头一段正文；
       snippet —— 命中偏弱：摘要 + 命中词附近的原文片段；
       recent  —— 完全没命中：只给最近 N 条，明确标注并限量（绝不全量硬塞）。
  3) 对话层：历史超预算时，把较早的对话压成摘要，原文落盘到 journal/，
     摘要里带块号（c1、c2…），需要时 `python harness.py log show c1` 回读全文。

配置优先级：环境变量 > config.json > 内置默认。
  MEMORY_DIR / MEMORY_LLM_BASE_URL / MEMORY_LLM_API_KEY / MEMORY_LLM_MODEL
"""
import argparse
import base64
import datetime
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid

try:  # Windows 控制台默认 GBK，避免打印中文报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VERSION = "2.9.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "config.json")

_DEFAULTS = {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4-flash",
    "models": [],                # 可选模型列表：[{"id": "...", "label": "..."}]，网页端下拉切换用
    "memory_dir": "",
    "retrieve_budget": "6000",   # 每次带进 prompt 的记忆字符数上限
    "history_budget": "8000",    # 对话历史字符数上限，超了就把旧对话压成摘要
    "auto_save": "auto",         # 记忆自动捕获：auto 直接存 / suggest 先问一句 / off 不猜
}
_ENV_KEYS = {
    "base_url": "MEMORY_LLM_BASE_URL",
    "api_key": "MEMORY_LLM_API_KEY",
    "model": "MEMORY_LLM_MODEL",
    "memory_dir": "MEMORY_DIR",
}


def load_config():
    """配置优先级：环境变量 > config.json > 内置默认。"""
    cfg = dict(_DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in _DEFAULTS})
        except Exception as e:
            print(f"(读取 {CONFIG_FILE} 失败，使用默认配置：{e})")
    for key, env in _ENV_KEYS.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


CFG = load_config()
BASE_URL = CFG["base_url"].rstrip("/")
API_KEY = CFG["api_key"] or os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
MODEL = CFG["model"]


def _norm_models(raw, current):
    """把配置里的 models 洗成 [{"id","label"}]，并保证当前模型一定在里面。"""
    out = []
    for item in raw or []:
        if isinstance(item, str) and item.strip():
            out.append({"id": item.strip(), "label": item.strip()})
        elif isinstance(item, dict) and item.get("id"):
            mid = str(item["id"]).strip()
            if mid:
                out.append({"id": mid, "label": str(item.get("label") or mid)})
    if not any(m["id"] == current for m in out):
        out.insert(0, {"id": current, "label": current})
    return out


MODELS = _norm_models(CFG.get("models"), MODEL)


def save_config(key, value):
    """把一项配置合并写进 config.json（原子写），已经存在的其它项保留。"""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg[key] = value
    _atomic_write(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    CFG[key] = value
    return cfg


def set_model(name):
    """切换当前模型：校验在 models 列表里，改内存里的 MODEL，并写回 config.json。"""
    global MODEL
    name = (name or "").strip()
    ids = [m["id"] for m in MODELS]
    if not name:
        raise ValueError("模型名不能为空")
    if ids and name not in ids:
        raise ValueError(f"未知模型 {name}，可选：{', '.join(ids)}")
    MODEL = name
    if not any(m["id"] == name for m in MODELS):
        MODELS.insert(0, {"id": name, "label": name})
    save_config("model", name)
    return MODEL


MEM_DIR = CFG["memory_dir"] or os.path.join(ROOT, "memories")
INDEX_FILE = os.path.join(MEM_DIR, "MEMORY.md")      # 给人看的索引
IDX_FILE = os.path.join(MEM_DIR, ".index.json")      # 给程序用的索引
TRASH_DIR = os.path.join(MEM_DIR, ".trash")
JOURNAL_DIR = os.path.join(ROOT, "journal")          # 对话原文（摘要里的块号指向这里）
IMG_WEIGHT = 1200                                    # 一张图折算多少字符的对话预算
MAX_IMAGE_BYTES = 6 * 1024 * 1024                     # 单张图片上限 6MB
INDEX_SCHEMA = 4                                      # 索引结构版本，变了就整个重建
TYPE_BOOST = {"user": 1.15, "feedback": 1.15, "project": 1.05, "reference": 0.95}
RECENCY_HALFLIFE = 120.0                              # 新近加成的半衰期（天）
RECENCY_WEIGHT = 0.15                                 # 最新条目最多加成 15%

TYPES = ("user", "feedback", "project", "reference")
STATUSES = ("active", "superseded")
FM_ORDER = ("name", "description", "type", "created", "updated", "source",
            "status", "supersedes", "superseded_by", "superseded_on")
MEM_EXTS = (".md", ".markdown", ".txt")
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

CHAT_SYS = ("你是基于本地 Markdown 记忆库的对话助手。回答尽量结合给出的记忆，"
            "记忆里没有的就直说，不要编造。回答保持简洁。"
            "用户发来图片时，直接看图回答（描述内容、认字、找问题都行），不要因为记忆里没写就拒答。")
ASK_SYS = ("你是基于本地记忆的问答助手。只依据给出的记忆回答，记忆里没有的就直说，"
           "不要编造。用户附了图片时，图片内容算作给出的信息，照实描述即可。"
           "回答末尾列出用到的记忆 name。")


# ---------- 存储层 ----------

def _atomic_write_bytes(path, data):
    """二进制版的原子写（图片用）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write(path, text):
    """原子写：写临时文件 → fsync → 改名覆盖。中途断电不会留下半个文件。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_text_file(path):
    """读 txt/md：先按 UTF-8（含 BOM），失败回退 GBK 系（Windows 记事本默认编码）。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_memory(text):
    """拆出 frontmatter 字典和正文。"""
    m = FM_RE.match(text)
    meta, body = {}, text
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta, body.strip()


def status_of(meta):
    """一条记忆的状态：默认 active，认识 superseded。"""
    s = str(meta.get("status") or "active").strip().lower()
    return s if s in STATUSES else "active"


def dump_memory(meta, body):
    """把 meta + 正文拼回 md。frontmatter 按 FM_ORDER 排，不认识的键跟在后面。"""
    keys = [k for k in FM_ORDER if k in meta]
    keys += [k for k in meta if k not in FM_ORDER and not k.startswith("_")]
    lines = ["---"] + [f"{k}: {_one_line(meta[k], 300)}" for k in keys] + ["---", "", body.strip(), ""]
    return "\n".join(lines)


def _set_meta(name, updates):
    """改写一条记忆的 frontmatter（正文不动）；值为 None 表示删掉这个键。"""
    found = find_memory(name)
    if not found:
        raise FileNotFoundError(f"找不到记忆 {name}")
    _, meta, body, path = found
    meta.pop("_file", None)
    for k, v in updates.items():
        if v is None:
            meta.pop(k, None)
        else:
            meta[k] = v
    _atomic_write(path, dump_memory(meta, body))
    rebuild_index()
    return meta


def supersede_memory(old, by):
    """把旧记忆标成"已被这条取代"；检索不再带它，但文件还在、能查能恢复。"""
    if not find_memory(old):
        raise FileNotFoundError(f"找不到记忆 {old}")
    return _set_meta(old, {"status": "superseded", "superseded_by": by,
                           "superseded_on": datetime.date.today().isoformat()})


def restore_memory(name):
    """把 superseded 的条目恢复成 active。"""
    return _set_meta(name, {"status": None, "superseded_by": None, "superseded_on": None})


def _memory_files():
    """memories/ 下所有记忆文件（跳过索引文件与隐藏文件）。"""
    out = []
    if os.path.isdir(MEM_DIR):
        for fn in sorted(os.listdir(MEM_DIR)):
            _, ext = os.path.splitext(fn)
            if ext.lower() not in MEM_EXTS or fn == "MEMORY.md" or fn.startswith("."):
                continue
            out.append(fn)
    return out


def read_all():
    """读取全部记忆，返回 [(stem, meta, body, path)]，按创建日期排序。"""
    items = []
    for fn in _memory_files():
        stem, ext = os.path.splitext(fn)
        path = os.path.join(MEM_DIR, fn)
        meta, body = parse_memory(_read_text_file(path))
        if ext.lower() != ".md" and not meta:
            # 手动放入的 txt：合成基础元数据（首行当描述，类型标记为 txt）
            first = next((l.strip() for l in body.splitlines() if l.strip()), stem)
            meta = {"name": stem, "description": first[:60],
                    "type": "txt", "created": datetime.date.today().isoformat()}
        meta["_file"] = fn
        items.append((stem, meta, body, path))
    items.sort(key=lambda x: x[1].get("created", ""))
    return items


def memory_path(name):
    """按 .md/.markdown/.txt 顺序定位记忆文件，找不到返回 None。"""
    for ext in MEM_EXTS:
        p = os.path.join(MEM_DIR, name + ext)
        if os.path.isfile(p):
            return p
    return None


def find_memory(name):
    """定位一条记忆：先按文件名，再按 frontmatter 里的 name（修掉导入后对不上的问题）。

    返回 (file_stem, meta, body, path) 或 None。
    """
    name = (name or "").strip()
    if not name:
        return None
    p = memory_path(name)
    if p:
        meta, body = parse_memory(_read_text_file(p))
        return os.path.splitext(os.path.basename(p))[0], meta, body, p
    for stem, meta, body, p in read_all():
        if name in (stem, meta.get("name", "")):
            return stem, meta, body, p
    return None


def slugify(text):
    s = re.sub(r"[^\w]+", "-", str(text).strip()).strip("-").lower()[:40]
    return s or "memo-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def unique_name(name):
    base, i = name, 2
    while find_memory(base):
        base = f"{name}-{i}"
        i += 1
    return base


def _one_line(text, limit=200):
    """把模型输出压成一行（frontmatter 里不能有换行）。"""
    return " ".join(str(text).split())[:limit]


def save_memory(name, description, mtype, body, source="", supersedes=""):
    os.makedirs(MEM_DIR, exist_ok=True)
    name = unique_name(slugify(name))
    path = os.path.join(MEM_DIR, name + ".md")
    today = datetime.date.today().isoformat()
    front = ("---\n"
             f"name: {name}\n"
             f"description: {_one_line(description, 200)}\n"
             f"type: {mtype}\n"
             f"created: {today}\n"
             f"updated: {today}\n")
    if source:
        front += f"source: {_one_line(source, 120)}\n"
    if supersedes:
        front += f"supersedes: {_one_line(supersedes, 80)}\n"
    front += "---\n\n"
    _atomic_write(path, front + body.strip() + "\n")
    if supersedes:
        try:
            supersede_memory(supersedes, name)     # 旧的那条降级，但文件保留
        except FileNotFoundError:
            pass                                    # 新条目已经写好了，旧名打错不该把这次保存弄失败
    rebuild_index()
    return name, path


def delete_memory(name, purge=False):
    """软删除：移到 .trash/；purge=True 时直接删掉。返回移走后的路径。"""
    found = find_memory(name)
    if not found:
        raise FileNotFoundError(f"找不到记忆 {name}")
    _, _, _, path = found
    if purge:
        os.remove(path)
        rebuild_index()
        return path
    os.makedirs(TRASH_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(TRASH_DIR, f"{stamp}-{os.path.basename(path)}")
    os.replace(path, target)
    rebuild_index()
    return target


def rebuild_index():
    """重建 MEMORY.md（给人看的索引）。机器索引见 build_index()。"""
    rebuild_md_index()


def rebuild_md_index(items=None):
    items = items if items is not None else read_all()
    lines = ["# 记忆索引", "",
             f"共 {len(items)} 条。本文件由 `python harness.py rebuild` 自动生成，手改会被覆盖。", ""]
    for n, m, _, _ in items:
        lines.append(f"- [{m.get('name', n)}]({m.get('_file', n + '.md')})"
                     f" — {m.get('description', '(无描述)')}")
    os.makedirs(MEM_DIR, exist_ok=True)
    _atomic_write(INDEX_FILE, "\n".join(lines) + "\n")


# ---------- 检索层 ----------

def tokenize(text):
    """粗分词：英文/数字按词，中文按二元组（bigram）。够本地几百条记忆用。"""
    t = (text or "").lower()
    toks = set()
    for w in re.findall(r"[a-z0-9][a-z0-9_\-\.]*", t):
        if len(w) >= 2:
            toks.add(w)
    for run in re.findall(f"[{CJK}]+", t):
        if len(run) == 1:
            toks.add(run)
        else:
            toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _signature():
    """磁盘快照，用来判断索引是否过期。"""
    sig = []
    for fn in _memory_files():
        p = os.path.join(MEM_DIR, fn)
        try:
            st = os.stat(p)
        except OSError:
            continue
        sig.append([fn, int(st.st_mtime), st.st_size])
    return sig


def build_index():
    """重建机器索引（.index.json）。除了分词，还统计文档频率给 IDF 用。"""
    sig = _signature()
    entries = []
    for stem, meta, body, path in read_all():
        head = f"{meta.get('name', stem)} {meta.get('description', '')}"
        entries.append({
            "file": meta.get("_file", stem + ".md"),
            "name": meta.get("name", stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "?"),
            "created": meta.get("created", ""),
            "status": status_of(meta),
            "superseded_by": meta.get("superseded_by", ""),
            "head_tokens": sorted(tokenize(head)),
            "tokens": sorted(tokenize(body)),
        })
    # 文档频率：每个 token 出现在多少条记忆里（判断「这个词是不是到处都有」）
    df = {}
    for e in entries:
        for t in set(e["tokens"]) | set(e["head_tokens"]):
            df[t] = df.get(t, 0) + 1
    idx = {"version": INDEX_SCHEMA, "generated": datetime.datetime.now().isoformat(timespec="seconds"),
           "signature": sig, "n": len(entries), "df": df, "entries": entries}
    os.makedirs(MEM_DIR, exist_ok=True)
    _atomic_write(IDX_FILE, json.dumps(idx, ensure_ascii=False, indent=1))
    return idx


def load_index(force=False):
    """读索引；过期、不存在或结构版本不对就重建。"""
    if not force and os.path.exists(IDX_FILE):
        try:
            with open(IDX_FILE, encoding="utf-8") as f:
                idx = json.load(f)
            if (idx.get("version") == INDEX_SCHEMA and idx.get("signature") == _signature()
                    and isinstance(idx.get("df"), dict)):
                return idx
        except Exception:
            pass
    return build_index()


def _idf(idx):
    """IDF 权重：doc-freq 越低的词越值钱。

    「用户喜欢」这种到处都有的 bigram 权重被压下去，
    「qwen2.5」这种只出现在一条记忆里的词权重高。零依赖，纯统计。
    """
    n = max(int(idx.get("n") or 0), 1)
    df = idx.get("df") or {}

    def weight(token):
        return math.log(1.0 + (n + 0.5) / (df.get(token, 0) + 0.5))

    return weight


def _recency_boost(created, today=None):
    """新近加成：刚存的记忆略占优，老条目慢慢回到 1.0（不会过期）。"""
    try:
        d = datetime.date.fromisoformat(str(created or "")[:10])
    except ValueError:
        return 1.0
    days = max(((today or datetime.date.today()) - d).days, 0)
    return 1.0 + RECENCY_WEIGHT * math.exp(-days / RECENCY_HALFLIFE)


def rank_entries(question, idx=None):
    """按 IDF 加权的相关度排序，返回 [(score, entry)]，只保留有命中的。

    分数 = 命中词权重 / 问题词总权重  （0~1）
         + 整句出现在名字/描述里再补一点
         × 类型权重（user/feedback 略高）
         × 新近加成（最多 +15%）

    注意：名字/描述的 token 本来就计入文档，"命中名字"不再额外加权——
    否则只匹配到「用户」这种高频词的条目会盖过匹配到专有词的条目。
    """
    idx = idx or load_index()
    q = tokenize(question)
    if not q:
        return []
    idf = _idf(idx)
    # 只拿「库里真有的词」当分母：问句里的生词（打错字、新词）不该稀释分数
    known = {t: idf(t) for t in q if (idx.get("df") or {}).get(t)}
    total = sum(known.values())
    if not total:
        return []
    phrase = (question or "").strip().lower()
    today = datetime.date.today()
    out = []
    for e in idx["entries"]:
        if e.get("status", "active") != "active":
            continue                       # superseded 的旧版不进检索，但仍能 show/恢复
        body = set(e.get("tokens") or [])
        head = set(e.get("head_tokens") or [])
        hit = set(known) & (body | head)   # 名字/描述也要能命中，不能只搜正文
        if not hit:
            continue
        score = sum(known[t] for t in hit) / total
        if phrase and phrase in (e.get("name", "") + e.get("description", "")).lower():
            score += 0.5
        score *= TYPE_BOOST.get(e.get("type", "?"), 1.0)
        score *= _recency_boost(e.get("created"), today)
        out.append((round(min(score, 1.5), 3), e))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _entry_body(entry):
    p = os.path.join(MEM_DIR, entry.get("file", ""))
    if os.path.isfile(p):
        return parse_memory(_read_text_file(p))[1]
    return ""


def _digest(entry, limit=400):
    body = _entry_body(entry)
    snippet = body[:limit] + ("…" if len(body) > limit else "")
    return (f"### {entry['name']}  [{entry.get('type', '?')}] {entry.get('description', '')}\n"
            f"{snippet}")


def _snippet(entry, question, limit=600):
    """摘要 + 命中词附近的原文片段。"""
    q = tokenize(question)
    lines = []
    for line in _entry_body(entry).splitlines():
        if tokenize(line) & q:
            lines.append(line.strip()[:160])
        if len(lines) >= 6:
            break
    body = "\n".join("  " + l for l in lines) if lines else _entry_body(entry)[:limit]
    return (f"### {entry['name']}  [{entry.get('type', '?')}] {entry.get('description', '')}\n"
            f"{body[:limit]}")


def usage_path():
    return os.path.join(MEM_DIR, ".usage.json")


def load_usage():
    """命中统计：{条目名: {"uses": 次数, "last": "YYYY-MM-DD"}}。

    单独存一个文件，而不是写回每条记忆的 frontmatter——写 md 会改 mtime，
    索引签名一变就得整库重建，检索一下重建一次太重了。
    """
    try:
        with open(usage_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def touch_usage(names):
    """记一次"这几条被检索用上了"。调用方保证 names 非空。"""
    if not names:
        return
    usage = load_usage()
    today = datetime.date.today().isoformat()
    for n in names:
        rec = usage.get(n) or {"uses": 0, "last": ""}
        rec["uses"] = int(rec.get("uses") or 0) + 1
        rec["last"] = today
        usage[n] = rec
    os.makedirs(MEM_DIR, exist_ok=True)
    try:
        _atomic_write(usage_path(), json.dumps(usage, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass                      # 统计写不进去不能拖累检索


def retrieve(question, budget_chars=None):
    """三级回退检索，返回 {text, tier, names, count}。

    digest：命中明确 → 摘要 + 开头正文
    snippet：命中偏弱 → 摘要 + 命中处片段
    recent：没命中 → 最近 N 条，明确标注并限量
    """
    budget_chars = budget_chars or int(CFG.get("retrieve_budget") or 6000)
    idx = load_index()
    if not idx["entries"]:
        return {"text": "", "tier": "empty", "names": [], "count": 0}

    ranked = rank_entries(question, idx)
    if ranked and ranked[0][0] >= 0.5:
        tier, picked = "digest", [e for _, e in ranked[:6]]
        render = lambda e: _digest(e)  # noqa: E731
    elif ranked:
        tier, picked = "snippet", [e for _, e in ranked[:6]]
        render = lambda e: _snippet(e, question)  # noqa: E731
    else:
        tier = "recent"
        picked = idx["entries"][-8:]
        render = lambda e: _digest(e)  # noqa: E731

    parts, used = [], 0
    for e in picked:
        chunk = render(e)
        if used + len(chunk) > budget_chars:
            break
        parts.append(chunk)
        used += len(chunk)

    header = {
        "digest": "",
        "snippet": "（以下是按关键词匹配到的片段，未匹配到的记忆本次未带入）\n",
        "recent": "（没有检索到直接相关的记忆，以下是最近的几条，仅供参考）\n",
    }[tier]
    text = header + "\n\n".join(parts) if parts else ""
    names = [e["name"] for e in picked[:len(parts)]]
    touch_usage(names)
    return {"text": text, "tier": tier, "names": names,
            "count": len(parts)}


def memory_corpus(question, max_items=6, budget=30000):
    """兼容旧接口：返回 (语料, 条数)。"""
    r = retrieve(question, budget_chars=min(int(CFG.get("retrieve_budget") or 6000), budget))
    return r["text"], r["count"]


def print_search(query):
    """有索引的搜索：按命中率排序，打印命中的行。"""
    idx = load_index()
    ranked = rank_entries(query, idx)
    if not ranked:
        print("（本地没有匹配；也可以直接问模型：python harness.py ask …）")
        return
    qt = tokenize(query)
    for score, e in ranked[:10]:
        print(f"[{score:.2f}] {e['name']}  ({e.get('type', '?')}, {e.get('created', '?')})")
        print(f"       {e.get('description', '')}")
        for line in _entry_body(e).splitlines():
            if tokenize(line) & qt:
                print(f"       > {line.strip()[:100]}")
                break
    print(f"\n命中 {len(ranked)} 条，索引：{IDX_FILE}")


# ---------- LLM 调用 ----------

def is_local(url):
    """本地/局域网推理服务（Ollama、LM Studio、vLLM 等），通常无需 API Key。"""
    host = urllib.parse.urlsplit(url).hostname or ""
    return (host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
            or host.endswith(".local")
            or host.startswith(("192.168.", "10.", "172.")))


def llm(messages, temperature=0.3):
    """调用 OpenAI 兼容 chat/completions 接口，返回文本。云端需 Key，本地服务可不填。"""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY
    elif not is_local(BASE_URL):
        raise RuntimeError("未配置 API Key（MEMORY_LLM_API_KEY / config set api_key ...）")
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps({"model": MODEL, "messages": messages,
                         "temperature": temperature}).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def llm_stream(messages, temperature=0.3):
    """流式版 llm()：逐段 yield 增量文本（SSE）。

    Ollama / OpenAI / 智谱都支持 `"stream": true`，返回 `data: {...}` 行，
    增量在 `choices[0].delta.content`。服务端不认流式（直接回 JSON）时，
    整段当一次产出，调用方无需区分。
    """
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY
    elif not is_local(BASE_URL):
        raise RuntimeError("未配置 API Key（MEMORY_LLM_API_KEY / config set api_key ...）")
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps({"model": MODEL, "messages": messages,
                         "temperature": temperature, "stream": True}).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "event-stream" not in ctype:            # 对方忽略了 stream，回的是整段 JSON
            data = json.loads(r.read().decode("utf-8"))
            yield data["choices"][0]["message"]["content"]
            return
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue                           # 空行（分隔符）和注释行跳过
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except ValueError:
                continue                           # 半截行/心跳，忽略
            choices = obj.get("choices") or []
            if not choices:
                continue
            piece = (choices[0].get("delta") or {}).get("content")
            if piece:
                yield piece


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型未返回 JSON")
    return json.loads(m.group(0))


def smart_meta(text):
    """让 LLM 为新记忆生成 name / description / type。"""
    names = ", ".join(m.get("name", n) for n, m, _, _ in read_all()[:200]) or "（暂无）"
    out = llm([
        {"role": "system", "content": "你负责整理个人记忆条目，只输出一个 JSON 对象，不要输出其它文字。"},
        {"role": "user", "content": (
            f"记忆内容：\n{text}\n\n"
            f"已有条目名（避免重名）：{names}\n\n"
            '请输出 {"name": "英文kebab-case短slug", '
            '"description": "一句话中文描述", '
            '"type": "user/feedback/project/reference 之一"}'
        )},
    ], temperature=0.2)
    meta = extract_json(out)
    return meta.get("name", ""), meta.get("description", text[:40]), meta.get("type", "project")


CAPTURE_SYS = """你是记忆提取器。看一轮对话（用户说的 + 助手答的），挑出**值得长期记住**的、
关于用户本人的稳定信息：偏好、习惯、身份、在做的项目、对助手的长期要求。

不要记：一次性的问题、临时的数字/时间、助手自己的话、常识、从上下文能直接推出来的东西。
宁缺毋滥——没有就返回空列表。

只输出 JSON：{"items": [{"name": "6-16 字中文短语", "description": "一句话",
"type": "user|feedback|project|reference", "body": "一两句，写结论不写过程"}]}
最多 2 条。"""

_PREF_HINT = re.compile(r"我(喜欢|不喜欢|偏好|讨厌|习惯|一般|通常|总是|讨厌|希望|要求|正在|在做|负责)|记[住下]|以后")


def capture_candidates(question, answer=""):
    """让模型判断这一轮有没有值得存进长期记忆的东西；返回候选列表（可能为空）。"""
    text = (question or "").strip()
    if len(text) < 6 and not _PREF_HINT.search(text):
        return []                       # 太短又不带偏好信号，不值得花一次调用
    try:
        out = llm([
            {"role": "system", "content": CAPTURE_SYS},
            {"role": "user", "content": f"用户：{text}\n助手：{(answer or '')[:400]}"},
        ], temperature=0.1)
        data = extract_json(out)
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out_items = []
    for it in items[:2]:
        if not isinstance(it, dict):
            continue
        body = str(it.get("body") or "").strip()
        name = str(it.get("name") or "").strip()
        if not body or not name:
            continue
        mtype = it.get("type") if it.get("type") in ("user", "feedback", "project", "reference") else "project"
        out_items.append({"name": name, "description": str(it.get("description") or body[:40]),
                          "type": mtype, "body": body})
    return out_items


def _already_known(name):
    """名字/内容跟已有记忆撞得太像就别重复存。"""
    try:
        ranked = rank_entries(name)
    except Exception:
        return False
    return bool(ranked) and ranked[0][0] >= 0.9

def _journal_path(block):
    return os.path.join(JOURNAL_DIR, f"{block}.md")


def _journal_write(block, text):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    path = _journal_path(block)
    head = (f"# 对话原文 #{block}\n\n"
            f"> 生成于 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，"
            f"被压缩进摘要的原始对话。\n\n")
    body = head + text + "\n"
    if os.path.exists(path):                      # 撞号也不覆盖，接着往下写
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n\n---\n\n" + body)
        return
    _atomic_write(path, body)


def list_journal():
    if not os.path.isdir(JOURNAL_DIR):
        return []
    return sorted((os.path.splitext(f)[0] for f in os.listdir(JOURNAL_DIR)
                   if f.endswith(".md") and not f.startswith(".")),
                  key=lambda s: (len(s), s))


SUMMARY_FILE = ".summary.md"


def summary_path():
    return os.path.join(JOURNAL_DIR, SUMMARY_FILE)


def load_summary():
    """读回磁盘上的压缩摘要；没有就是空串。"""
    p = summary_path()
    if not os.path.isfile(p):
        return ""
    text = _read_text_file(p)
    return text if (not text or text.endswith("\n")) else text + "\n"


def save_summary(text):
    """把整份摘要原子写进 journal/.summary.md —— 关掉网页也不丢。"""
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    _atomic_write(summary_path(), text)


# ---------- 图片（多模态输入） ----------

_IMG_EXT = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "webp": "webp", "gif": "gif"}
_IMG_RE = re.compile(r"\Adata:image/([A-Za-z0-9.+-]+);base64,", re.I)


def img_dir():
    return os.path.join(JOURNAL_DIR, "images")


def img_decode(data_url):
    """data:image/png;base64,xxx → (扩展名, 原始字节)。"""
    m = _IMG_RE.match(data_url or "")
    if not m:
        raise ValueError("图片必须是 data:image/*;base64, 形式")
    ext = _IMG_EXT.get(m.group(1).lower())
    if not ext:
        raise ValueError(f"只支持 png/jpg/webp/gif，收到 {m.group(1)}")
    try:
        raw = base64.b64decode(data_url[m.end():], validate=True)
    except Exception:
        raise ValueError("图片 base64 解不开")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片太大（{len(raw)} 字节，上限 {MAX_IMAGE_BYTES}）")
    return ext, raw


def file_to_data_url(path):
    """本地图片文件 → data URL（命令行用）。"""
    if not os.path.isfile(path):
        raise SystemExit(f"找不到图片：{path}")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = _IMG_EXT.get(ext)
    if not mime:
        raise SystemExit(f"只支持 png/jpg/webp/gif：{path}")
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) > MAX_IMAGE_BYTES:
        raise SystemExit(f"图片太大（{len(raw)} 字节，上限 {MAX_IMAGE_BYTES}）")
    return f"data:image/{mime};base64," + base64.b64encode(raw).decode()


def persist_image(data_url):
    """把图片落盘到 journal/images/，返回相对路径。

    原文只留路径，绝不把 base64 写进 md——否则 journal 会被几十兆的字符串撑爆。
    """
    ext, raw = img_decode(data_url)
    os.makedirs(img_dir(), exist_ok=True)
    name = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.{ext}"
    path = os.path.join(img_dir(), name)
    _atomic_write_bytes(path, raw)
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def text_of(content):
    """把 str 或 OpenAI 多模态 parts 统一成纯文本。"""
    if isinstance(content, str):
        return content
    out = []
    for p in content or []:
        if isinstance(p, dict) and p.get("type") == "text":
            out.append(p.get("text") or "")
    return "\n".join(x for x in out if x)


def count_images(content):
    if not isinstance(content, list):
        return 0
    return sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")


def _local_summary(text, max_chars=400):
    """没有 LLM 时的降级摘要：保留头尾，中间省略。有 LLM 时不用它。"""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    head, tail = int(max_chars * 0.7), int(max_chars * 0.3)
    return flat[:head] + " …(中间省略)… " + flat[-tail:]


def summarize_text(text, max_chars=400):
    """用 LLM 把一段对话压成几行；失败则降级为本地截断（绝不静默丢内容）。"""
    try:
        if API_KEY or is_local(BASE_URL):
            out = llm([
                {"role": "system", "content": "你在压缩聊天记录。只输出 3 行以内的要点，"
                                              "保留：结论、决定和理由、提到的路径/命令/数字。不要寒暄。"},
                {"role": "user", "content": text[:12000]},
            ], temperature=0.2)
            out = out.strip()
            if out:
                return out[:max_chars]
    except Exception as e:
        print(f"(摘要调用失败，改用本地截断：{e})", file=sys.stderr)
    return _local_summary(text, max_chars)


class ChatSession:
    """一次对话的上下文管理：超预算就把最早的几轮压成摘要，原文进 journal/。"""

    def __init__(self, budget=None, summary="", keep_recent=4, blocks=None):
        self.messages = []
        self.summary = summary or ""
        # 块号接着 journal/ 里已有的往下数，重启或网页刷新都不会重号
        self.blocks = list(blocks) if blocks else list_journal()
        self.budget = int(budget or CFG.get("history_budget") or 8000)
        self.keep_recent = keep_recent

    # -- 序列化（网页端来回传） --
    @classmethod
    def from_state(cls, messages=None, summary="", blocks=None):
        s = cls(summary=summary, blocks=blocks)
        s.messages = [m for m in (messages or [])
                      if m.get("role") in ("user", "assistant") and m.get("content")]
        if not s.messages and not s.summary:
            # 网页刚打开 / 刷新过头：把磁盘上的摘要捡回来，不让之前的压缩白压
            s.summary = load_summary()
        return s

    def state(self):
        # 图片只回传文字标记（路径），base64 不进浏览器 state，否则每条请求都是几兆
        return {"history": [{"role": m["role"], "content": text_of(m["content"])}
                            for m in self.messages],
                "summary": self.summary, "blocks": self.blocks}

    # -- 压缩 --
    def size(self):
        total = 0
        for m in self.messages:
            total += len(text_of(m["content"]))
            total += IMG_WEIGHT * count_images(m["content"])
        return total

    def compact_if_needed(self):
        if self.size() <= self.budget or len(self.messages) <= self.keep_recent:
            return False
        chunk = self.messages[:-self.keep_recent]
        self.messages = self.messages[-self.keep_recent:]
        text = "\n".join(f"{'用户' if m['role'] == 'user' else '助手'}: {text_of(m['content'])}"
                         for m in chunk)
        block = f"c{len(self.blocks) + 1}"
        _journal_write(block, text)
        self.blocks.append(block)
        self.summary += f"- [{block}] {summarize_text(text)}\n"
        save_summary(self.summary)          # 摘要落盘：网页关了、进程重启了都能捡回来
        return True

    # -- 提问 --
    def prepare(self, question, images=None):
        """检索记忆、拼 system prompt、把这一轮的 user 消息建好（不调用模型）。"""
        r = retrieve(question)
        sys_prompt = CHAT_SYS
        if r["text"]:
            sys_prompt += f"\n\n# 本地记忆（{r['tier']} 级，{r['count']} 条）\n{r['text']}"
        if self.summary:
            sys_prompt += f"\n\n# 较早对话的摘要（原文可用 `log show <块号>` 回读）\n{self.summary}"
        content = question
        if images:
            parts = [{"type": "text", "text": question}]
            for url in images:
                rel = persist_image(url)
                parts.append({"type": "text", "text": f"[图片 {rel}]"})
                parts.append({"type": "image_url", "image_url": {"url": url}})
            content = parts
        msgs = [{"role": "system", "content": sys_prompt}] + self.messages + \
               [{"role": "user", "content": content}]
        return msgs, content, r

    def finish(self, question, content, answer, r):
        """一轮问答收尾：入历史 → 超预算就压缩 → 判断要不要记记忆。"""
        self.messages += [{"role": "user", "content": content},
                          {"role": "assistant", "content": answer}]
        self.compact_if_needed()
        r["saved"], r["suggested"] = self.capture_memory(question, answer)
        return r

    def ask(self, question, images=None):
        """images：data URL 列表。图片落盘到 journal/images/，进 prompt 用 image_url。"""
        msgs, content, r = self.prepare(question, images)
        answer = llm(msgs, temperature=0.6)
        return answer, self.finish(question, content, answer, r)

    def ask_stream(self, question, images=None):
        """流式提问：边跑边 yield 增量文本，结束把最终结果放进 self.last_result。"""
        msgs, content, r = self.prepare(question, images)
        buf = []
        for piece in llm_stream(msgs, temperature=0.6):
            buf.append(piece)
            yield piece
        answer = "".join(buf)
        self.last_result = (answer, self.finish(question, content, answer, r))

    def capture_memory(self, question, answer):
        """按 auto_save 配置处理"这轮有没有该记的事"：off 不猜，suggest 给候选，auto 直接存。"""
        mode = str(CFG.get("auto_save") or "suggest").lower()
        if mode not in ("suggest", "auto") or count_images(question):
            return [], []
        cands = [c for c in capture_candidates(question, answer) if not _already_known(c["name"])]
        if not cands:
            return [], []
        if mode == "auto":
            saved = []
            for c in cands:
                try:
                    save_memory(c["name"], c["description"], c["type"], c["body"],
                                source="chat 自动捕获")
                    saved.append(c["name"])
                except Exception:
                    pass
            return saved, []
        return [], cands


# ---------- 共用逻辑 ----------

def do_save(text, mtype=None, use_llm=True, source="手动保存", supersedes=""):
    """保存一条记忆；use_llm 时让模型起名/写摘要。返回条目名。"""
    name, desc = text[:20], text[:40]
    if use_llm:
        if not API_KEY and not is_local(BASE_URL):
            print("(未配置 API Key，纯本地保存：名字/描述为内容截断)")
        else:
            try:
                name, desc, t = smart_meta(text)
                if t in TYPES:
                    mtype = t
            except Exception as e:
                print(f"(LLM 加工失败，回退本地模式：{e})")
    name, path = save_memory(name, desc, mtype or "project", text, source=source,
                             supersedes=supersedes)
    print(f"已保存 {path}")
    print(f"  name: {name}\n  description: {desc}")
    return name


def import_text(filename, content, mtype=None, use_llm=True):
    """把一段文本导入为一条记忆；md 自带 frontmatter 时直接复用其元数据。"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    meta, body = parse_memory(content)
    body = body.strip() or stem
    if meta.get("name"):
        name = meta["name"]
        desc = meta.get("description") or body[:60]
        t = meta.get("type") if meta.get("type") in TYPES else (mtype or "project")
    elif use_llm and (API_KEY or is_local(BASE_URL)):
        sample = body if len(body) <= 3000 else body[:3000] + "…(已截断)"
        try:
            name, desc, t = smart_meta(sample)
            if t not in TYPES:
                t = mtype or "project"
        except Exception as e:
            print(f"(LLM 起名失败，回退文件名：{e})")
            name, desc, t = stem, body[:60], (mtype or "project")
    else:
        name, desc, t = stem, body[:60], (mtype or "project")
    name, path = save_memory(name, desc, t, body, source=f"导入 {os.path.basename(filename)}")
    print(f"已导入 {path}")
    print(f"  name: {name}\n  description: {desc}\n  type: {t}")
    return name


def print_memories():
    items = read_all()
    usage = load_usage()
    if not items:
        print("（还没有记忆，用 save 或 /save 存一条）")
        return
    for n, m, _, _ in items:
        u = usage.get(n) or {}
        used = f"  ·被检索 {u['uses']} 次（{u.get('last', '')}）" if u.get("uses") else ""
        gone = (f"  ⤳ 已被 {m.get('superseded_by') or '新版'} 取代"
                if status_of(m) == "superseded" else "")
        print(f"{n}  [{m.get('type', '?')}] {m.get('description', '')}  "
              f"({m.get('created', '?')}){used}{gone}")
    print(f"\n共 {len(items)} 条，索引：{INDEX_FILE}")


# ---------- 子命令 ----------

def cmd_save(a):
    do_save(" ".join(a.text).strip(), mtype=a.type, use_llm=not a.no_llm,
            source="命令行 save", supersedes=getattr(a, "supersedes", "") or "")


def cmd_supersede(a):
    """存一条新版，并把旧版降级（旧文件保留，检索不再带它）。"""
    text = " ".join(a.text).strip()
    if not text:
        sys.exit("新内容不能为空")
    if not find_memory(a.old):
        sys.exit(f"找不到旧记忆 {a.old}")
    name = do_save(text, mtype=a.type, use_llm=not a.no_llm,
                   source="命令行 supersede", supersedes=a.old)
    print(f"已用 {name} 取代 {a.old}（旧版仍在 memories/，可 show / restore 回看）")


def cmd_restore(a):
    meta = restore_memory(a.name)
    print(f"已恢复 {meta.get('name', a.name)} 为 active")


def cmd_list(a):
    print_memories()


def cmd_search(a):
    print_search(a.query)


def cmd_show(a):
    found = find_memory(a.name)
    if not found:
        sys.exit(f"找不到记忆 {a.name}")
    print(_read_text_file(found[3]))


def cmd_ask(a):
    question = " ".join(a.question)
    images = [file_to_data_url(p) for p in (getattr(a, "image", None) or [])]
    r = retrieve(question)
    if r["count"] == 0 and not images:
        sys.exit("本地还没有记忆。")
    print(f"[模型 {MODEL} | {r['tier']} 级检索，带入 {r['count']} 条：{', '.join(r['names'])}]")
    content = f"# 记忆库\n{r['text']}\n\n# 问题\n{question}"
    if images:
        content = [{"type": "text", "text": content}]
        for url in images:
            content.append({"type": "image_url", "image_url": {"url": url}})
        print(f"[附 {len(images)} 张图]")
    msgs = [
        {"role": "system", "content": ASK_SYS},
        {"role": "user", "content": content},
    ]
    if getattr(a, "no_stream", False):
        print(llm(msgs))
        return
    try:
        for piece in llm_stream(msgs):          # 先流一次，失败再退回整段
            print(piece, end="", flush=True)
        print()
    except Exception as e:
        print(f"\n(流式失败，改用整段：{e})")
        print(llm(msgs))


def cmd_chat(a):
    idx = load_index()
    sess = ChatSession(budget=a.budget)
    if not sess.summary:                 # 上次聊完压过的摘要，接着用
        sess.summary = load_summary()
    pending = [file_to_data_url(p) for p in (getattr(a, "image", None) or [])]
    print(f"md-memory-harness {VERSION} 对话模式")
    print(f"模型 {MODEL} @ {BASE_URL} | 记忆 {len(idx['entries'])} 条 | "
          f"历史预算 {sess.budget} 字符（超了自动摘要）")
    print("命令：/save <内容> 存记忆 | /search <词> | /list | /history 看摘要 | "
          "/img <图片路径> 挂图 | /new 清空 | /exit 退出")
    if pending:
        print(f"（已挂 {len(pending)} 张图，随第一句一起发）")
    print()
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "/q"):
            break
        if line == "/new":
            sess = ChatSession(budget=sess.budget)
            pending = []
            print("(已清空本轮对话，记忆保留)\n")
            continue
        if line == "/list":
            print_memories()
            print()
            continue
        if line == "/history":
            print(sess.summary or "（还没有压缩过，完整对话都在上下文里）")
            print(f"块号：{', '.join(sess.blocks) or '（无）'}\n")
            continue
        if line.startswith("/img"):
            path = line[len("/img"):].strip()
            if not path:
                print("用法：/img C:\\path\\pic.png（可连挂多张，下一句一起发）\n")
                continue
            try:
                pending.append(file_to_data_url(path))
            except SystemExit as e:
                print(f"({e})\n")
                continue
            print(f"(已挂 {len(pending)} 张图，下一句一起发)\n")
            continue
        if line.startswith("/search "):
            print_search(line[len("/search "):].strip())
            print()
            continue
        if line.startswith("/save "):
            text = line[len("/save "):].strip()
            if text:
                do_save(text)
            print()
            continue
        try:
            if getattr(a, "no_stream", False):
                answer, r = sess.ask(line, pending)
                print(f"AI> {answer}")
            else:
                print("AI> ", end="", flush=True)   # 逐字出，收尾时自己补换行
                for piece in sess.ask_stream(line, pending):
                    print(piece, end="", flush=True)
                answer, r = sess.last_result
                print()
            pending = []
        except ValueError as e:
            print(f"(图片有问题：{e})")
            print()
            continue
        except Exception as e:
            print(f"(调用失败：{e})")
            if pending:
                print("   提示：如果报的是 vision / image 相关错误，"
                      "说明当前模型（%s）不支持读图，换 qwen2.5-vl、glm-4v、gpt-4o 这类多模态模型。" % MODEL)
            print()
            continue
        if r["tier"] == "recent":
            print(f"   （记忆里没搜到相关的，只带了最近 {r['count']} 条）")
        if r.get("saved"):
            print(f"   🧠 已自动记住：{'、'.join(r['saved'])}")
        for c in r.get("suggested") or []:
            print(f"   🧠 要记一条吗？{c['name']}：{c['description']}（/save 后回车可手动存）")
        print()
    if sess.blocks:
        print(f"本次压缩了 {len(sess.blocks)} 块对话，原文在 {JOURNAL_DIR}，"
              f"用 python harness.py log show {sess.blocks[0]} 可回读。")
    print("再见。")


def cmd_log(a):
    blocks = list_journal()
    if not a.show:
        if not blocks:
            print("（还没有压缩过对话；连续聊到超预算时会自动生成）")
            return
        for b in blocks:
            p = _journal_path(b)
            print(f"{b}  {os.path.getsize(p)} 字节  {p}")
        print(f"\n回读全文：python harness.py log show {blocks[-1]}")
        return
    p = _journal_path(a.show)
    if not os.path.isfile(p):
        sys.exit(f"找不到块 {a.show}")
    print(_read_text_file(p))


def cmd_config(a):
    args = list(a.set_args)
    if args and args[0] == "set":  # 兼容 "config set key value" 写法
        args = args[1:]
    if not args:
        print(f"base_url       = {BASE_URL}")
        print(f"model          = {MODEL}")
        if len(MODELS) > 1:
            print("可选模型       = " + " / ".join(f"{m['id']}（{m['label']}）" if m["label"] != m["id"] else m["id"]
                                                 for m in MODELS))
        print(f"api_key        = {'已设置' if API_KEY else '（未设置，本地服务可不设）'}")
        print(f"memory_dir     = {MEM_DIR}")
        print(f"retrieve_budget= {CFG.get('retrieve_budget')} 字符（每次带进 prompt 的记忆上限）")
        print(f"history_budget = {CFG.get('history_budget')} 字符（对话历史超了自动摘要）")
        print(f"auto_save      = {CFG.get('auto_save')}（auto 自动存记忆 / suggest 先问 / off 只手动）")
        print(f"\n配置文件：{CONFIG_FILE}")
        print("优先级：环境变量 > config.json > 默认")
        print("设置示例：python harness.py config set base_url http://127.0.0.1:11434/v1")
        print("切换模型：python harness.py config set model glm-4-flash（或网页端点右上角下拉）")
        return
    if len(args) != 2:
        sys.exit("用法：config            查看配置\n      config set <key> <value>  写入配置\n"
                 f"可选 key：{' / '.join(_DEFAULTS)}")
    key, value = args
    if key not in _DEFAULTS:
        sys.exit(f"未知配置项 {key}，可选：{' / '.join(_DEFAULTS)}")
    if key == "model":
        try:
            set_model(value)
        except ValueError as e:
            sys.exit(str(e))
        print(f"已切换到模型 {MODEL}（已写入 {CONFIG_FILE}）")
        return
    save_config(key, value)
    print(f"已写入 {CONFIG_FILE}: {key} = {value}")
    print("（环境变量优先于 config.json；下次运行命令即生效）")


def cmd_delete(a):
    target = delete_memory(a.name, purge=a.purge)
    print(f"已{'彻底删除' if a.purge else '移到回收站'} {target}")


def cmd_rebuild(a):
    idx = build_index()
    rebuild_md_index()
    print(f"已重建索引：{len(idx['entries'])} 条 → {IDX_FILE}")
    print(f"人类可读索引：{INDEX_FILE}")


def cmd_import(a):
    ok = 0
    for p in a.files:
        if not os.path.isfile(p):
            print(f"跳过（文件不存在）：{p}")
            continue
        if os.path.splitext(p)[1].lower() not in (".txt", ".md", ".markdown"):
            print(f"跳过（仅支持 .txt/.md）：{p}")
            continue
        content = _read_text_file(p)
        if not content.strip():
            print(f"跳过（空文件）：{p}")
            continue
        import_text(p, content, mtype=a.type, use_llm=not a.no_llm)
        ok += 1
    print(f"共导入 {ok} 个文件 → {MEM_DIR}")


def main():
    ap = argparse.ArgumentParser(
        prog="harness.py",
        description=f"md-memory-harness {VERSION} — 本地 Markdown 记忆（分层检索 + 对话摘要压缩）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("save", help="存一条记忆（默认调 LLM 起名/写摘要）")
    p.add_argument("text", nargs="+", help="记忆内容")
    p.add_argument("--type", default="project", choices=TYPES, help="记忆类型，默认 project")
    p.add_argument("--no-llm", action="store_true", help="不调 API，纯本地保存")
    p.add_argument("--supersedes", default="", metavar="旧名",
                   help="这条替代哪条旧记忆（旧版会被标成 superseded，检索不再带它）")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("supersede", help="存新版并降级旧版（supersede 旧名 新内容）")
    p.add_argument("old", help="被取代的旧记忆名")
    p.add_argument("text", nargs="+", help="新版内容")
    p.add_argument("--type", default="project", choices=TYPES, help="记忆类型，默认 project")
    p.add_argument("--no-llm", action="store_true", help="不调 API，纯本地保存")
    p.set_defaults(func=cmd_supersede)

    p = sub.add_parser("restore", help="把 superseded 的条目恢复成 active")
    p.add_argument("name", help="记忆名")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("chat", help="对话模式：带记忆的连续对话（超预算自动摘要）")
    p.add_argument("--budget", type=int, default=None, help="历史字符预算，默认取配置")
    p.add_argument("--image", action="append", metavar="路径",
                   help="开场先挂一张图（可重复；对话中也可用 /img <路径>）")
    p.add_argument("--no-stream", action="store_true", help="等模型说完再整段显示（默认逐字出字）")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("ask", help="带着记忆问 LLM（单次问答）")
    p.add_argument("question", nargs="+", help="问题")
    p.add_argument("--image", action="append", metavar="路径",
                   help="附一张图（可重复），需要模型支持读图")
    p.add_argument("--no-stream", action="store_true", help="等模型说完再整段显示（默认逐字出字）")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("list", help="列出全部记忆")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="按索引搜索记忆（中文按二元组匹配）")
    p.add_argument("query", help="关键词")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="查看某条记忆全文")
    p.add_argument("name", help="条目名（文件名或 frontmatter name，不含扩展名）")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("config", help="查看配置，或 config set <key> <value> 写入 config.json")
    p.add_argument("set_args", nargs="*", help="如：config set model qwen2.5:7b")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("delete", help="删除记忆（默认移到 .trash/，可回捞）")
    p.add_argument("name", help="条目名（不含扩展名）")
    p.add_argument("--purge", action="store_true", help="彻底删除，不进回收站")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("rebuild", help="重建 MEMORY.md 与 .index.json")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("log", help="查看对话压缩留下的原文块（log show c1 回读全文）")
    p.add_argument("show", nargs="?", default=None, help="块号，如 c1；省略则列出全部")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("import", help="导入 .txt/.md 文件为记忆（可一次多个；md 自带 frontmatter 时直接复用）")
    p.add_argument("files", nargs="+", help="文件路径")
    p.add_argument("--type", default=None, choices=TYPES, help="指定类型；默认自动")
    p.add_argument("--no-llm", action="store_true", help="不调 API，用文件名当名字、首行当描述")
    p.set_defaults(func=cmd_import)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
