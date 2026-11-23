"""端口检查：演示服务的端口在不在监听、该空的端口是不是被占了。"""

from __future__ import annotations

from pre_sales_kit.core.proc import can_connect, find_listener
from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = ("host", "port", "expect", "timeout")


@check("port", "TCP 端口：是否在监听 / 是否被占用")
def run_port(spec: CheckSpec, ctx: CheckContext) -> list[Item]:  # noqa: ARG001 - ctx 是检查协议的一部分
    params = Params.of(spec, _ALLOWED)
    host = params.text("host", "127.0.0.1") or "127.0.0.1"
    port = params.integer("port", required=True, minimum=1, maximum=65535)
    expect = params.text("expect", "listening", choices=("listening", "free"))
    timeout = params.number("timeout", 1.0, minimum=0.05, maximum=30.0) or 1.0
    assert port is not None

    label = f"{host}:{port}"
    reachable, elapsed, reason = can_connect(host, port, timeout)
    timing = f"{elapsed * 1000:.0f}ms"

    if expect == "listening":
        if reachable:
            owner = ""
            listener = find_listener(port) if host in {"127.0.0.1", "localhost", "0.0.0.0", "::"} else None
            if listener and listener.pid:
                process = f" · {listener.process or '未知进程'}(PID {listener.pid})"
                owner = process
            return [Item(label, Status.PASS, f"{label} 正在监听 · {timing}{owner}")]
        return [
            Item(
                label,
                Status.FAIL,
                f"{label} 未监听：{reason}",
                hint="演示服务没起来。在 YAML 里配 fix: start_service 可以自动拉起",
            )
        ]

    # expect == "free"
    if reachable:
        listener = find_listener(port)
        if listener and listener.pid:
            who = f"{listener.process or '未知进程'}(PID {listener.pid})"
            return [
                Item(
                    label,
                    Status.FAIL,
                    f"{label} 已被占用 · {who}",
                    hint="端口冲突会让演示服务起不来；可配 fix: kill_port 释放",
                )
            ]
        return [
            Item(
                label,
                Status.FAIL,
                f"{label} 已被占用（拿不到进程信息，可能需要管理员权限）",
                hint="用一个不冲突的端口，或手工结束占用进程",
            )
        ]
    return [Item(label, Status.PASS, f"{label} 未被占用")]
