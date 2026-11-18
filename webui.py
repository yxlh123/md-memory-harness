#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆Harness 本地网页界面。

启动：python webui.py [端口] [--no-open]    默认端口 8765，自动打开浏览器
数据与配置完全复用 harness.py；仅监听 127.0.0.1，无外部依赖。
"""
import importlib
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import harness  # noqa: E402

UI_FILE = os.path.join(ROOT, "ui.html")
DEFAULT_PORT = 8765


def api_state():
    return {
        "base_url": harness.BASE_URL,
        "model": harness.MODEL,
        "has_key": bool(harness.API_KEY),
        "is_local": harness.is_local(harness.BASE_URL),
        "memory_dir": harness.MEM_DIR,
        "memories": [{"name": n, "type": m.get("type", "?"),
                      "description": m.get("description", ""),
                      "created": m.get("created", "")}
                     for n, m, _, _ in harness.read_all()],
    }


def api_error(msg):
    return {"error": msg}


def handle_api(path, body):
    """分发 POST /api/*，返回结果 dict。异常向上抛，由 Handler 统一转 500。"""
    if path == "/api/chat":
        msg = body.get("message", "").strip()
        if not msg:
            return api_error("消息为空")
        history = [h for h in body.get("history", [])
                   if h.get("role") in ("user", "assistant") and h.get("content")][-24:]
        corpus, count = harness.memory_corpus(msg)
        prefix = harness.CHAT_SYS + ("\n\n# 本地记忆\n" + corpus if corpus else "")
        messages = [{"role": "system", "content": prefix}] + history + [{"role": "user", "content": msg}]
        return {"reply": harness.llm(messages, temperature=0.6), "used_memories": count}

    if path == "/api/save":
        text = body.get("text", "").strip()
        if not text:
            return api_error("内容为空")
        name = harness.do_save(text, mtype=body.get("type") or None,
                               use_llm=bool(body.get("use_llm", True)))
        return {"name": name}

    if path == "/api/delete":
        name = body.get("name", "")
        if (not name or os.path.basename(name) != name or name == "MEMORY"
                or name.startswith(".")):
            return api_error("无效的条目名")
        path_md = harness.memory_path(name)  # 支持 .md/.markdown/.txt
        if not path_md:
            return api_error("找不到该记忆")
        os.remove(path_md)
        harness.rebuild_index()
        return {"ok": True}

    if path == "/api/import":
        filename = body.get("filename", "")
        content = body.get("content") or ""
        if not filename or not content.strip():
            return api_error("文件名为空或内容为空")
        if len(content) > 200_000:
            return api_error("文件过大（>200KB），请拆分后导入")
        name = harness.import_text(filename, content, mtype=body.get("type") or None,
                                   use_llm=bool(body.get("use_llm", True)))
        return {"name": name}

    if path == "/api/config":
        key, value = body.get("key", ""), body.get("value")
        if key not in harness._DEFAULTS or value is None:
            return api_error("无效配置项")
        cfg = {}
        if os.path.exists(harness.CONFIG_FILE):
            with open(harness.CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        cfg[key] = str(value)
        with open(harness.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        importlib.reload(harness)  # 新配置立即生效
        return {"ok": True, "state": api_state()}

    return api_error("未知接口")


class Handler(BaseHTTPRequestHandler):
    server_version = "MemoryHarnessUI/1.0"

    def _send(self, code, ctype, payload):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/", "/index.html"):
            with open(UI_FILE, encoding="utf-8") as f:
                self._send(200, "text/html; charset=utf-8", f.read().encode("utf-8"))
        elif path == "/api/state":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(api_state(), ensure_ascii=False).encode("utf-8"))
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            result = handle_api(path, body)
            code = 200
        except Exception as e:
            result, code = api_error(str(e)), 500
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(result, ensure_ascii=False).encode("utf-8"))

    def log_message(self, *args):  # 静默访问日志
        pass


def main():
    args = sys.argv[1:]
    port = DEFAULT_PORT
    if args and args[0].isdigit():
        port = int(args[0])
    open_browser = "--no-open" not in args
    url = f"http://127.0.0.1:{port}/"
    print(f"记忆Harness 界面: {url}   (Ctrl+C 退出)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
