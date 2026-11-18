#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 Markdown 记忆 harness。

记忆以 .md 文件存本地（memories/ 目录 + MEMORY.md 索引），
"理解/加工"类步骤（起名、摘要、对话、问答）通过 LLM API 完成。

用法示例：
  python harness.py save 用户偏好深色主题 --type user
  python harness.py list
  python harness.py search 深色
  python harness.py show user-preference-dark-theme
  python harness.py ask 用户有什么偏好？
  python harness.py chat          # 连续对话模式
  python harness.py config        # 查看/设置配置（写入 config.json）
  python harness.py delete user-preference-dark-theme
  python harness.py rebuild

配置优先级：环境变量 > config.json > 内置默认。
  MEMORY_DIR / MEMORY_LLM_BASE_URL / MEMORY_LLM_API_KEY / MEMORY_LLM_MODEL
  config.json 可用 config set 写入：base_url / api_key / model / memory_dir
  本地服务（Ollama、LM Studio 等）无需 api_key。
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

try:  # Windows 控制台默认 GBK，避免打印中文报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "config.json")

_DEFAULTS = {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4-flash",
    "memory_dir": "",
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
MEM_DIR = CFG["memory_dir"] or os.path.join(ROOT, "memories")
INDEX_FILE = os.path.join(MEM_DIR, "MEMORY.md")

TYPES = ("user", "feedback", "project", "reference")
MEM_EXTS = (".md", ".markdown", ".txt")
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def memory_path(name):
    """按 .md/.markdown/.txt 顺序定位记忆文件，找不到返回 None。"""
    for ext in MEM_EXTS:
        p = os.path.join(MEM_DIR, name + ext)
        if os.path.isfile(p):
            return p
    return None

CHAT_SYS = ("你是基于本地 Markdown 记忆库的对话助手。回答尽量结合给出的记忆，"
            "记忆里没有的就直说，不要编造。回答保持简洁。")


# ---------- 本地存储 ----------

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


def read_all():
    """读取全部记忆（.md/.markdown/.txt），返回 [(name, meta, body, path)]，按创建日期排序。"""
    items = []
    if os.path.isdir(MEM_DIR):
        for fn in sorted(os.listdir(MEM_DIR)):
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in MEM_EXTS or fn == "MEMORY.md" or fn.startswith("."):
                continue
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


def slugify(text):
    s = re.sub(r"[^\w]+", "-", str(text).strip()).strip("-").lower()[:40]
    return s or "memo-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def unique_name(name):
    base, i = name, 2
    while memory_path(base):
        base = f"{name}-{i}"
        i += 1
    return base


def save_memory(name, description, mtype, body):
    os.makedirs(MEM_DIR, exist_ok=True)
    name = unique_name(slugify(name))
    path = os.path.join(MEM_DIR, name + ".md")
    front = ("---\n"
             f"name: {name}\n"
             f"description: {description}\n"
             f"type: {mtype}\n"
             f"created: {datetime.date.today().isoformat()}\n"
             "---\n\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(front + body.strip() + "\n")
    return name, path


def rebuild_index(items=None):
    items = items if items is not None else read_all()
    lines = ["# 记忆索引", ""]
    lines += [f"- [{m.get('name', n)}]({m.get('_file', n + '.md')}) — {m.get('description', '(无描述)')}"
              for n, m, _, _ in items]
    os.makedirs(MEM_DIR, exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型未返回 JSON")
    return json.loads(m.group(0))


def smart_meta(text):
    """让 LLM 为新记忆生成 name / description / type。"""
    names = ", ".join(m.get("name", n) for n, m, _, _ in read_all()) or "（暂无）"
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


# ---------- 共用逻辑 ----------

def memory_corpus(question, max_items=30, budget=30000):
    """拼出记忆语料；记忆太多时按关键词粗筛。返回 (语料, 条数)。"""
    items = read_all()
    if not items:
        return "", 0
    corpus = "\n\n".join(f"### {m.get('name', n)}\n{b}" for n, m, b, _ in items)
    if len(corpus) > budget:
        toks = [t for t in re.split(r"[\s，。？！,.?!]+", question) if len(t) > 1]
        def score(item):
            n, m, b, _ = item
            t = (n + m.get("description", "") + b).lower()
            return sum(t.count(t2.lower()) for t2 in toks)
        items = sorted(items, key=score, reverse=True)[:max_items]
        corpus = "\n\n".join(f"### {m.get('name', n)}\n{b}" for n, m, b, _ in items)
    return corpus, len(items)


def do_save(text, mtype=None, use_llm=True):
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
    name, path = save_memory(name, desc, mtype or "project", text)
    rebuild_index()
    print(f"已保存 {path}")
    print(f"  name: {name}\n  description: {desc}")
    return name


def print_memories():
    items = read_all()
    if not items:
        print("（还没有记忆，用 save 或 /save 存一条）")
        return
    for n, m, _, _ in items:
        print(f"{n}  [{m.get('type', '?')}] {m.get('description', '')}  ({m.get('created', '?')})")
    print(f"\n共 {len(items)} 条，索引：{INDEX_FILE}")


def print_search(query):
    q = query.lower()
    hits = 0
    for n, m, b, p in read_all():
        text = (n + "\n" + m.get("description", "") + "\n" + b).lower()
        if q not in text:
            continue
        hits += 1
        print(f"[{n}] {p}")
        for line in b.splitlines():
            if q in line.lower():
                print("    " + line.strip()[:100])
    if not hits:
        print("（本地没有匹配；也可以用 ask / 对话模式直接问模型）")


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
    name, path = save_memory(name, desc, t, body)
    rebuild_index()
    print(f"已导入 {path}")
    print(f"  name: {name}\n  description: {desc}\n  type: {t}")
    return name


# ---------- 子命令 ----------

def cmd_save(a):
    do_save(" ".join(a.text).strip(), mtype=a.type, use_llm=not a.no_llm)


def cmd_list(a):
    print_memories()


def cmd_search(a):
    print_search(a.query)


def cmd_show(a):
    path = memory_path(a.name)
    if not path:
        sys.exit(f"找不到记忆 {a.name}")
    print(_read_text_file(path))


def cmd_ask(a):
    question = " ".join(a.question)
    corpus, count = memory_corpus(question)
    if count == 0:
        sys.exit("本地还没有记忆。")
    print(f"[模型 {MODEL}，带入 {count} 条记忆]")
    answer = llm([
        {"role": "system",
         "content": "你是基于本地记忆的问答助手。只依据给出的记忆回答，记忆里没有的就直说，不要编造。回答末尾列出用到的记忆 name。"},
        {"role": "user", "content": f"# 记忆库\n{corpus}\n\n# 问题\n{question}"},
    ])
    print(answer)


def cmd_chat(a):
    _, count = memory_corpus("")
    print("记忆Harness 对话模式")
    print(f"模型 {MODEL} @ {BASE_URL} | 记忆 {count} 条 | 本轮对话为临时上下文")
    print("命令：/save <内容> 存记忆 | /search <词> | /list | /new 清空对话 | /exit 退出\n")
    history = []
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
            history = []
            print("(已清空本轮对话，记忆保留)\n")
            continue
        if line == "/list":
            print_memories()
            print()
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
        history.append({"role": "user", "content": line})
        corpus, count = memory_corpus(line)
        prefix = f"{CHAT_SYS}\n\n# 本地记忆\n{corpus}" if corpus else CHAT_SYS
        try:
            answer = llm([{"role": "system", "content": prefix}] + history[-24:],
                         temperature=0.6)
        except Exception as e:
            print(f"(调用失败：{e})\n")
            history.pop()
            continue
        print(f"AI> {answer}\n")
        history.append({"role": "assistant", "content": answer})
    print("再见。")


def cmd_config(a):
    args = list(a.set_args)
    if args and args[0] == "set":  # 兼容 "config set key value" 写法
        args = args[1:]
    if not args:
        print(f"base_url   = {BASE_URL}")
        print(f"model      = {MODEL}")
        print(f"api_key    = {'已设置' if API_KEY else '（未设置，本地服务可不设）'}")
        print(f"memory_dir = {MEM_DIR}")
        print(f"\n配置文件：{CONFIG_FILE}")
        print("优先级：环境变量 > config.json > 默认")
        print("设置示例：python harness.py config set base_url http://127.0.0.1:11434/v1")
        return
    if len(args) != 2:
        sys.exit("用法：config            查看配置\n      config set <key> <value>  写入配置\n"
                 f"可选 key：{' / '.join(_DEFAULTS)}")
    key, value = args
    if key not in _DEFAULTS:
        sys.exit(f"未知配置项 {key}，可选：{' / '.join(_DEFAULTS)}")
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg[key] = value
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"已写入 {CONFIG_FILE}: {key} = {value}")
    print("（环境变量优先于 config.json；下次运行命令即生效）")


def cmd_delete(a):
    path = memory_path(a.name)
    if not path:
        sys.exit(f"找不到记忆 {a.name}")
    os.remove(path)
    rebuild_index()
    print(f"已删除 {path}")


def cmd_rebuild(a):
    rebuild_index()
    print(f"已重建 {INDEX_FILE}")


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
    ap = argparse.ArgumentParser(description="本地 Markdown 记忆 harness（存储在本地，加工走 LLM API）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("save", help="存一条记忆（默认调 LLM 起名/写摘要）")
    p.add_argument("text", nargs="+", help="记忆内容")
    p.add_argument("--type", default="project", choices=TYPES, help="记忆类型，默认 project")
    p.add_argument("--no-llm", action="store_true", help="不调 API，纯本地保存")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("chat", help="对话模式：带记忆的连续对话")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("ask", help="带着记忆问 LLM（单次问答）")
    p.add_argument("question", nargs="+", help="问题")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("list", help="列出全部记忆")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="本地关键词搜索")
    p.add_argument("query", help="关键词")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="查看某条记忆全文")
    p.add_argument("name", help="条目名（不含 .md）")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("config", help="查看配置，或 config set <key> <value> 写入 config.json")
    p.add_argument("set_args", nargs="*", help="如：config set model qwen2.5:7b")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("delete", help="删除某条记忆")
    p.add_argument("name", help="条目名（不含 .md）")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("rebuild", help="根据 md 文件重建 MEMORY.md 索引")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("import", help="导入 .txt/.md 文件为记忆（可一次多个；md 自带 frontmatter 时直接复用）")
    p.add_argument("files", nargs="+", help="文件路径")
    p.add_argument("--type", default=None, choices=TYPES, help="指定类型；默认自动（md 元数据 > LLM 判断 > project）")
    p.add_argument("--no-llm", action="store_true", help="不调 API，用文件名当名字、首行当描述")
    p.set_defaults(func=cmd_import)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
