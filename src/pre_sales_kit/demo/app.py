"""演示用的最小服务：给 kit doctor 一个真实的探测对象。

两种用法：
    kit demo up                                  # 推荐：kit 负责启停和状态
    python -m pre_sales_kit.demo --port 8080     # 手动起一个，前台占着

只用标准库，不额外装任何东西；客户现场拿它顶一下也能用。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: 默认读当前目录下的 data/demo.db（与 doctor.yaml 里的 data 检查同一路径）
DEFAULT_DB = Path("data") / "demo.db"

LIMIT_ROWS = 20


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def recent_measurements(db: Path, limit: int = LIMIT_ROWS) -> list[dict[str, object]]:
    """读最近的点位数据；库不存在或表不存在都返回空列表，不报错。

    演示门户在数据库还没生成时也要能打开——先看到页面，再看到数据。
    """
    if not db.exists():
        return []
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
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
    return [{"ts": row[0], "point": row[1], "value": row[2], "unit": row[3]} for row in rows]


class Handler(BaseHTTPRequestHandler):
    server_version = "DemoApp/1.1"
    role = "portal"
    db_path = DEFAULT_DB

    # ---- 路由 ----
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/ping"):
            self._json({"status": "ok", "service": self.role, "time": _now()})
        elif path == "/api/measurements":
            items = recent_measurements(self.db_path)
            self._json({"count": len(items), "items": items})
        elif path == "/":
            self._html(_landing_page(self.role, self.db_path))
        else:
            self._json({"error": "not found", "path": path}, status=404)

    def do_HEAD(self) -> None:
        self._json({"status": "ok", "service": self.role}, body=False)

    # ---- 输出 ----
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

    def log_message(self, format: str, *args: object) -> None:
        """默认会把每个请求打到 stderr，演示时太吵，关掉。"""


def _landing_page(role: str, db_path: Path) -> str:
    items = recent_measurements(db_path, 5)
    rows = (
        "".join(
            f"<tr><td>{item['ts']}</td><td>{item['point']}</td>"
            f"<td>{item['value']}</td><td>{item['unit']}</td></tr>"
            for item in items
        )
        or '<tr><td colspan="4" class="empty">还没有演示数据，先跑 kit seed</td></tr>'
    )
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
<p>角色 <code>{role}</code> · 这是配套示例，不是真实产品界面。健康检查：<code>/health</code></p>
<table><thead><tr><th>时间</th><th>点位</th><th>数值</th><th>单位</th></tr></thead>
<tbody>{rows}</tbody></table>
</main></body></html>"""


class DemoServer(ThreadingHTTPServer):
    # Windows 上 allow_reuse_address 会让第二个进程悄悄接管同一个端口，
    # 现场排查时非常容易误判；这里改成「占用了就明确失败」。
    allow_reuse_address = False
    daemon_threads = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pre_sales_kit.demo",
        description="演示用最小 HTTP 服务（仅供现场演示与自检）",
    )
    parser.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认只监听本机）")
    parser.add_argument(
        "--role", default="portal", choices=("portal", "alarm"), help="服务角色，只影响页面与 /health 的字段"
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"演示数据库路径（默认 {DEFAULT_DB}）",
    )
    return parser


def serve(port: int, *, host: str = "127.0.0.1", role: str = "portal", db: str | Path = DEFAULT_DB) -> None:
    """前台跑起来，直到 Ctrl+C。端口被占直接报错退出。"""
    Handler.role = role
    Handler.db_path = Path(db)
    try:
        server = DemoServer((host, port), Handler)
    except OSError as exc:
        raise SystemExit(
            f"[demo] 端口 {port} 无法监听：{exc}\n"
            f"       先看是谁占着：kit demo status，或 kit doctor --only 演示"
        ) from exc
    print(
        f"[demo] {role} listening on http://{host}:{port} · db={Handler.db_path}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    serve(args.port, host=args.host, role=args.role, db=args.db)


if __name__ == "__main__":
    main()
