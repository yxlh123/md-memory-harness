#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md-memory-harness 本地网页界面。

启动：python webui.py [端口] [--no-open]    默认端口 8765，自动打开浏览器
数据和配置完全复用 harness.py；只监听 127.0.0.1，无第三方依赖。

与命令行版的区别只有一处：网页端把对话历史（history + summary）放在浏览器里，
每次请求带上来，服务端用 harness.ChatSession 做同样的「超预算就摘要」处理，
再把压缩后的 history / summary 传回去，所以刷新页面之前不会丢上下文。
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import harness  # noqa: E402

UI_FILE = os.path.join(ROOT, "ui.html")
DEFAULT_PORT = 8765


def api_state():
    idx = harness.load_index()
    return {
        "version": harness.VERSION,
        "base_url": harness.BASE_URL,
        "model": harness.MODEL,
        "has_key": bool(harness.API_KEY),
        "is_local": harness.is_local(harness.BASE_URL),
        "memory_dir": harness.MEM_DIR,
        "journal": len(harness.list_journal()),
        "history_budget": int(harness.CFG.get("history_budget") or 8000),
        "memories": [{"name": n, "type": m.get("type", "?"),
                      "description": m.get("description", ""),
                      "created": m.get("created", "")}
                     for n, m, _, _ in harness.read_all()],
        "indexed": len(idx["entries"]),
    }


def api_error(msg):
    return {"error": msg}


def handle_api(path, body):
    """分发 POST /api/*，返回结果 dict。异常向上抛，由 Handler 统一转 500。"""
    if path == "/api/chat":
        msg = body.get("message", "").strip()
        if not msg:
            return api_error("消息为空")
        sess = harness.ChatSession.from_state(body.get("history", []), body.get("summary", ""))
        try:
            reply, r = sess.ask(msg)
        except Exception as e:
            return api_error(f"调用模型失败：{e}")
        out = {"reply": reply, "tier": r["tier"], "used_memories": r["count"],
               "used_names": r["names"]}
        out.update(sess.state())          # history / summary / blocks 回传给浏览器保存
        return out

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
        try:
            target = harness.delete_memory(name, purge=bool(body.get("purge")))
        except FileNotFoundError as e:
            return api_error(str(e))
        return {"ok": True, "path": target}

    if path == "/api/import":
        filename = body.get("filename", "")
        content = body.get("content") or ""
        if not filename or not content.strip():
            return api_error("文件名为空或内容为空")
        if os.path.basename(filename) != filename:
            return api_error("文件名不能带路径")
        if len(content) > 200_000:
            return api_error("文件过大（>200KB），请拆分后导入")
        name = harness.import_text(filename, content, mtype=body.get("type") or None,
                                   use_llm=bool(body.get("use_llm", True)))
        return {"name": name}

    if path == "/api/search":
        q = body.get("query", "").strip()
        ranked = harness.rank_entries(q)
        return {"results": [{"score": s, "name": e["name"], "type": e.get("type", "?"),
                             "description": e.get("description", "")}
                            for s, e in ranked[:20]]}

    if path == "/api/config":
        key, value = body.get("key", ""), body.get("value")
        if key not in harness._DEFAULTS or value is None:
            return api_error("无效配置项")
        cfg = {}
        if os.path.exists(harness.CONFIG_FILE):
            with open(harness.CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        cfg[key] = str(value)
        harness._atomic_write(harness.CONFIG_FILE,
                              json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        importlib.reload(harness)  # 新配置立即生效
        return {"ok": True, "state": api_state()}

    return api_error("未知接口")


class Handler(BaseHTTPRequestHandler):
    server_version = "MemoryHarnessUI/2.0"

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
    print(f"md-memory-harness 界面: {url}   (Ctrl+C 退出)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
