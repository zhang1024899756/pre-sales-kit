"""kit demo / demo 管理器：起、停、查。

这里有一条必须守住的线：端口上如果是别人的进程，绝不动手。
客户机器上的 8080 可能跑着他们真实的服务。
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pre_sales_kit.cli import app
from pre_sales_kit.core.errors import KitError
from pre_sales_kit.core.proc import find_listener
from pre_sales_kit.demo import manager

runner = CliRunner()


def test_start_describe_stop(tmp_path: Path, free_port: int) -> None:
    try:
        outcomes = manager.start(tmp_path, [("portal", free_port)], wait=20)
        assert [item.status for item in outcomes] == ["started"], outcomes[0].detail

        state = manager.load_state(manager.state_path(tmp_path))
        record = state.find("portal")
        assert record is not None
        assert record.port == free_port

        word, detail = manager.describe(record)
        assert word == "running", detail
    finally:
        manager.stop(tmp_path)

    assert find_listener(free_port) is None
    assert manager.load_state(manager.state_path(tmp_path)).empty


def test_port_taken_by_foreign_process_is_not_killed(tmp_path: Path) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    port = sock.getsockname()[1]
    try:
        outcomes = manager.start(tmp_path, [("portal", port)], wait=3)
        assert outcomes[0].status == "failed"
        assert "没有动它" in outcomes[0].detail
        # 我们的监听还在：说明它真的没被碰
        listener = find_listener(port)
        assert listener is not None
    finally:
        sock.close()


def test_stop_skips_process_that_is_not_ours(tmp_path: Path) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    port = sock.getsockname()[1]
    try:
        path = manager.state_path(tmp_path)
        manager.save_state(
            path,
            manager.DemoState(
                services=[
                    manager.ServiceRecord(role="portal", port=port, pid=999_999, started="2026-01-01T00:00:00")
                ]
            ),
        )
        outcomes = manager.stop(tmp_path)
        assert outcomes[0].status == "skipped"
        assert "没动那个进程" in outcomes[0].detail
        assert find_listener(port) is not None
        assert manager.load_state(path).empty
    finally:
        sock.close()


def test_corrupt_state_file_explains_itself(tmp_path: Path) -> None:
    path = manager.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 json", encoding="utf-8")
    with pytest.raises(KitError) as caught:
        manager.load_state(path)
    assert str(path) in str(caught.value)
    assert caught.value.hint


def test_status_reports_foreign_listener_that_kit_does_not_own(tmp_path: Path) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    port = sock.getsockname()[1]
    try:
        rows, _hints = manager.status(tmp_path)
        assert rows == []
        occupied = manager.probe_port(port)
        assert occupied is not None and "占用" in occupied
    finally:
        sock.close()


def test_cli_up_status_down(tmp_path: Path, free_port: int) -> None:
    try:
        up = runner.invoke(
            app, ["demo", "up", "--service", f"portal={free_port}", "--dir", str(tmp_path), "--wait", "20"]
        )
        assert up.exit_code == 0, up.output

        status = runner.invoke(app, ["demo", "status", "--dir", str(tmp_path), "--json"])
        assert status.exit_code == 0, status.output
        payload = json.loads(status.output)
        assert payload["services"][0]["state"] == "running"
        assert payload["services"][0]["port"] == free_port
    finally:
        down = runner.invoke(app, ["demo", "down", "--dir", str(tmp_path)])
    assert down.exit_code == 0, down.output
    assert find_listener(free_port) is None


def test_cli_status_without_state_is_not_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "status", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "没有记录" in result.output


def test_cli_rejects_bad_service_syntax(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "up", "--service", "portal=abc", "--dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "不是数字" in result.output
