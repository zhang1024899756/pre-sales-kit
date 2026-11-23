"""测试公共设施：临时声明文件、真端口、真 HTTP 服务。"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pre_sales_kit.doctor.models import CheckContext, CheckSpec


@pytest.fixture
def ctx(tmp_path: Path) -> CheckContext:
    return CheckContext(
        spec_path=tmp_path / "spec.yaml",
        base_dir=tmp_path,
        now=datetime.now().astimezone(),
    )


@pytest.fixture
def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def listening_port() -> Iterator[int]:
    """真的开一个监听端口，测「端口在不在」不用 mock。"""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, '{"status": "ok", "service": "test"}')
        elif self.path == "/bad":
            self._send(503, '{"status": "degraded"}')
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/health")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/string":
            self._send(200, "hello")
        else:
            self._send(404, '{"error": "not found"}')

    def _send(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """静音：测试里不需要每个请求的日志。"""


@pytest.fixture
def http_endpoint() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def make_spec(
    type_name: str,
    params: dict[str, Any] | None = None,
    *,
    name: str = "测试检查",
    fix: Any = None,
    tags: tuple[str, ...] = (),
    source: str = "test.yaml",
    check_id: str = "test",
) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        name=name,
        type=type_name,
        params=params or {},
        fix=fix,
        tags=tags,
        source=source,
    )


@pytest.fixture
def write_yaml(tmp_path: Path):
    def _write(name: str, content: str) -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write
