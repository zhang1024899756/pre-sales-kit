"""网络检查：客户现场的内网连通性。

现场最常见的两种翻车：数据库在客户内网但 VPN 掉了；演示依赖外网 CDN 而机房没开。
"""

from __future__ import annotations

from pre_sales_kit.core.proc import can_connect
from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = ("targets", "timeout")
_TARGET_KEYS = ("name", "host", "port", "expect", "timeout")


@check("network", "网络：内网 / 外网的 TCP 可达性")
def run_network(spec: CheckSpec, ctx: CheckContext) -> list[Item]:  # noqa: ARG001 - ctx 是检查协议的一部分
    params = Params.of(spec, _ALLOWED)
    default_timeout = params.number("timeout", 2.0, minimum=0.1, maximum=30.0) or 2.0
    targets = params.sequence("targets")
    if not targets:
        from pre_sales_kit.core.errors import SpecError

        raise SpecError(
            message=f"检查「{spec.name}」（{spec.id}）的 network.targets 是空的，至少要写一个目标",
            path=spec.source,
        )

    items: list[Item] = []
    for index, raw in enumerate(targets, start=1):
        if not isinstance(raw, dict):
            raise Params._spec_error(
                spec, f"params.targets[{index}]", "应该是键值映射（name / host / port）"
            )
        unknown = sorted(set(raw) - set(_TARGET_KEYS))
        if unknown:
            raise Params._spec_error(
                spec,
                f"params.targets[{index}]",
                f"出现未知字段 {', '.join(unknown)}；可用：{', '.join(_TARGET_KEYS)}",
            )
        host = raw.get("host")
        if not isinstance(host, str) or not host.strip():
            raise Params._spec_error(spec, f"params.targets[{index}].host", "必须是非空字符串")
        port = raw.get("port")
        if not isinstance(port, int) or isinstance(port, bool):
            raise Params._spec_error(spec, f"params.targets[{index}].port", "必须是整数端口")
        expect = str(raw.get("expect", "reachable"))
        if expect not in ("reachable", "unreachable"):
            raise Params._spec_error(
                spec, f"params.targets[{index}].expect", "只能是 reachable 或 unreachable"
            )
        timeout = raw.get("timeout", default_timeout)
        name = str(raw.get("name") or f"{host}:{port}")
        label = str(raw.get("name") or host)

        reachable, elapsed, reason = can_connect(str(host), int(port), float(timeout))
        timing = f"{elapsed * 1000:.0f}ms"
        if expect == "reachable":
            if reachable:
                items.append(Item(label, Status.PASS, f"{name} 可达 · {timing}"))
            else:
                items.append(
                    Item(
                        label,
                        Status.FAIL,
                        f"{name} 不可达：{reason}",
                        hint="先确认 VPN / 网段 / 防火墙；现场不通就改用本机演示环境",
                    )
                )
        else:
            if reachable:
                items.append(
                    Item(
                        label,
                        Status.WARN,
                        f"{name} 居然可达（声明里要求不可达）",
                        hint="如果演示不该连外网，检查一下网络出口",
                    )
                )
            else:
                items.append(Item(label, Status.PASS, f"{name} 不可达（符合预期）"))
    return items
