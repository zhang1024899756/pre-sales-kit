"""演示用的最小服务：给 kit doctor 一个真实的探测对象。

用法：
    python examples/demo_app.py --port 8080                 # 演示门户
    python examples/demo_app.py --port 6379 --role alarm    # 报警通知服务

只用标准库，不需要装任何东西；客户现场拿它顶一下也能用。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "demo.db"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _recent_measurements(limit: int = 20) -> list[dict[str, object]]:
    """从演示库里读最近的点位数据；库不存在就返回空列表，不报错。"""
    if not DEFAULT_DB.exists():
        return []
    try:
        connection = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            "SELECT ts, point, value, unit FROM measurements ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [
        {"ts": row[0], "point": row[1], "value": row[2], "unit": row[3]} for row in rows
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "DemoApp/1.0"
    role = "portal"

    def do_GET(self) -> None:  # noqa: N802 - http.server 的约定
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/ping"):
            self._json({"status": "ok", "service": self.role, "time": _now()})
        elif path == "/api/measurements":
            items = _recent_measurements()
            self._json({"count": len(items), "items": items})
        elif path == "/":
            self._html(_landing_page())
        else:
            self._json({"error": "not found", "path": path}, status=404)

    def do_HEAD(self) -> None:  # noqa: N802
        self._json({"status": "ok", "service": self.role}, body=False)

    def _json(self, payload: dict[str, object], *, status: int = 200, body: bool = True) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(data)

    def _html(self, text: str, *, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """默认会把每个请求打到 stderr，演示时太吵，关掉。"""


def _landing_page() -> str:
    items = _recent_measurements(5)
    rows = "".join(
        f"<tr><td>{item['ts']}</td><td>{item['point']}</td>"
        f"<td>{item['value']}</td><td>{item['unit']}</td></tr>"
        for item in items
    ) or '<tr><td colspan="4" class="empty">还没有演示数据，先跑 kit seed</td></tr>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>演示门户</title>
<style>
body{{font-family:ui-sans-serif,"Microsoft YaHei UI",sans-serif;margin:0;padding:40px;
background:#fbfaf7;color:#16181d}}
main{{max-width:720px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}}
p{{color:#666b75;font-size:13px;margin:0 0 24px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #e4e1d8}}
th{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#666b75}}
.empty{{color:#666b75}}
code{{background:#efeee9;padding:1px 5px;border-radius:2px}}
</style></head>
<body><main>
<h1>产线能耗与设备监控 · 演示门户</h1>
<p>这是配套示例，不是真实产品界面。健康检查：<code>/health</code></p>
<table><thead><tr><th>时间</th><th>点位</th><th>数值</th><th>单位</th></tr></thead>
<tbody>{rows}</tbody></table>
</main></body></html>"""


class _Server(ThreadingHTTPServer):
    # Windows 上 allow_reuse_address 会让第二个进程悄悄接管同一个端口，
    # 现场排查时非常容易误判；这里改成「占用了就明确失败」。
    allow_reuse_address = False
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="演示用最小 HTTP 服务")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--role", default="portal", choices=("portal", "alarm"))
    args = parser.parse_args()

    Handler.role = args.role
    try:
        server = _Server((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"[demo_app] 端口 {args.port} 无法监听：{exc}") from exc
    print(f"[demo_app] {args.role} listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
