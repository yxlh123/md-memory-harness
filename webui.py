#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md-memory-harness 本地网页界面。

启动：python webui.py [端口] [--no-open]    默认端口 8765，自动打开浏览器
数据和配置完全复用 harness.py；只监听 127.0.0.1，无第三方依赖。

与命令行版的区别只有一处：网页端把对话历史（history + summary）放在浏览器里，
每次请求带上来，服务端用 harness.ChatSession 做同样的「超预算就摘要」处理，
再把压缩后的 history / summary 传回去，所以刷新页面之前不会丢上下文。
"""
import argparse
import importlib
import json
import os
import secrets
import socket
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
TOKEN = ""          # 非空时所有请求都要带口令（?k=xxx 或 X-Token 头）


def _private(ip):
    """只留家用/办公网段：10.x、172.16-31.x、192.168.x（169.254 这种自动私有地址没用）。"""
    if ip.startswith("10.") or ip.startswith("192.168."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (ValueError, IndexError):
            return False
    return False


def lan_ips():
    """猜出本机的局域网 IPv4（不发包，只是让系统选出口网卡）。默认路由那张网卡排最前。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return [ip for ip in ips if _private(ip)]


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


MAX_IMAGES = 4                      # 一条消息最多几张图
MAX_BODY = 24 * 1024 * 1024         # 整个请求体上限（4 张 6MB 的图，base64 后约 32MB，留够）


def clean_images(raw):
    """校验前端传来的图片 data URL；返回 (图片列表, 错误信息)。"""
    if not raw:
        return [], None
    if not isinstance(raw, list):
        return [], "images 必须是数组"
    if len(raw) > MAX_IMAGES:
        return [], f"一条消息最多 {MAX_IMAGES} 张图"
    out = []
    for url in raw:
        if not isinstance(url, str) or not url.startswith("data:image/"):
            return [], "图片格式不对（需要 data:image/...;base64,...）"
        try:
            harness.img_decode(url)     # 只校验，不落盘；落盘在 ChatSession.ask 里做
        except ValueError as e:
            return [], str(e)
        out.append(url)
    return out, None


def handle_api(path, body):
    """分发 POST /api/*，返回结果 dict。异常向上抛，由 Handler 统一转 500。"""
    if path == "/api/chat":
        msg = body.get("message", "").strip()
        if not msg:
            return api_error("消息为空")
        images, err = clean_images(body.get("images"))
        if err:
            return api_error(err)
        sess = harness.ChatSession.from_state(body.get("history", []), body.get("summary", ""),
                                              body.get("blocks", []))
        try:
            reply, r = sess.ask(msg, images)
        except Exception as e:
            return api_error(f"调用模型失败：{e}")
        out = {"reply": reply, "tier": r["tier"], "used_memories": r["count"],
               "used_names": r["names"], "images": len(images)}
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
    server_version = "MemoryHarnessUI/2.1"

    def _send(self, code, ctype, payload):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authed(self, query):
        """口令校验：URL 上的 ?k=xxx，或者请求头 X-Token。没设口令时一律放行。"""
        if not TOKEN:
            return True
        got = (urllib.parse.parse_qs(query).get("k") or [""])[0] or self.headers.get("X-Token", "")
        return secrets.compare_digest(got, TOKEN)

    def _deny(self, is_api):
        if is_api:
            payload = json.dumps(api_error("没有访问口令，或者口令不对"), ensure_ascii=False).encode("utf-8")
            self._send(403, "application/json; charset=utf-8", payload)
            return
        html = ("<!doctype html><meta charset='utf-8'><title>需要口令</title>"
                "<body style='font-family:system-ui;padding:40px;line-height:1.7'>"
                "<h2>需要访问口令</h2>"
                "<p>把启动 <code>webui.py</code> 的那个窗口里打印的地址<b>整条</b>复制过来"
                "（形如 <code>http://192.168.1.5:8765/?k=abcd2345</code>），口令就在链接里。</p>"
                "</body>").encode("utf-8")
        self._send(403, "text/html; charset=utf-8", html)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path, query = parsed.path, parsed.query
        if not self._authed(query):
            self._deny(is_api=path.startswith("/api/"))
            return
        if path in ("/", "/index.html"):
            with open(UI_FILE, encoding="utf-8") as f:
                self._send(200, "text/html; charset=utf-8", f.read().encode("utf-8"))
        elif path == "/api/state":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(api_state(), ensure_ascii=False).encode("utf-8"))
        elif path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not self._authed(parsed.query):
            self._deny(is_api=True)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY:
            # 先把请求体读掉再回错误：不然对方还在写，连接会被 reset，前端看不到这条消息
            left = min(length, MAX_BODY + 16 * 1024 * 1024)
            while left > 0:
                chunk = self.rfile.read(min(1 << 16, left))
                if not chunk:
                    break
                left -= len(chunk)
            self.close_connection = True
            self._send(413, "application/json; charset=utf-8",
                       json.dumps(api_error(f"请求太大（{length} 字节，上限 {MAX_BODY}）："
                                            f"图片别超过 {MAX_IMAGES} 张、单张 6MB"),
                                  ensure_ascii=False).encode("utf-8"))
            return
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
    ap = argparse.ArgumentParser(
        description="md-memory-harness 本地网页界面",
        epilog="例：python webui.py --lan        # 同一个 WiFi 下手机也能打开")
    ap.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT, help="端口，默认 8765")
    ap.add_argument("--lan", action="store_true", help="允许同一局域网（WiFi）访问")
    ap.add_argument("--host", default=None, help="监听地址，默认 127.0.0.1；--lan 等价于 0.0.0.0")
    ap.add_argument("--token", default=None, help="访问口令；--lan 时不填就自动生成一个")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    a = ap.parse_args()

    host = a.host or ("0.0.0.0" if a.lan else "127.0.0.1")
    lan = host not in ("127.0.0.1", "localhost", "::1")
    if lan and not a.token:
        a.token = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
    global TOKEN
    TOKEN = a.token or ""

    local_url = f"http://127.0.0.1:{a.port}/"
    def say(*x):
        print(*x, flush=True)          # 后台跑的时候也要能立刻看到输出
    say(f"md-memory-harness {harness.VERSION} 界面: {local_url}   (Ctrl+C 退出)")
    if lan:
        ips = lan_ips()
        q = f"?k={TOKEN}" if TOKEN else ""
        say("\n同一个 WiFi 下，手机/别的电脑用下面这个地址打开：")
        if ips:
            for ip in ips:
                say(f"  http://{ip}:{a.port}/{q}")
        else:
            say("  （没找到局域网 IP，看看是不是没连 WiFi；也可以用 ipconfig 查 IPv4）")
        if TOKEN:
            say(f"\n访问口令：{TOKEN}（已经带在上面链接的 ?k= 里，别把链接发给别人）")
        say("Windows 第一次可能会弹防火墙提示，要选「允许访问」，否则手机会连不上。")
        say("这个界面没有账号体系，口令就是唯一保护：网里任何人都能拿它看你的记忆。\n")
    if not a.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(local_url + (f"?k={TOKEN}" if TOKEN else ""))).start()
    ThreadingHTTPServer((host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
