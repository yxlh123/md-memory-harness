#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极简 OpenAI 兼容 mock 服务：在没有真实 API Key 时测试 harness 的 LLM 路径。

用法：
  python mock_llm.py [端口]        # 默认 8765
另开终端：
  MEMORY_LLM_BASE_URL=http://127.0.0.1:8765/v1 python harness.py save 测试内容
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        blob = json.dumps(req.get("messages", []), ensure_ascii=False)
        if "JSON" in blob:  # smart_meta 的起名请求
            content = json.dumps({"name": "mock-test-entry",
                                  "description": "mock 生成的描述，验证 LLM 路径",
                                  "type": "project"}, ensure_ascii=False)
        else:  # ask 的问答请求
            content = "【mock 回答】根据本地记忆：用户偏好深色主题和简洁回答。"
        body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                    "content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默访问日志
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock LLM 监听 http://127.0.0.1:{port}/v1/chat/completions")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
