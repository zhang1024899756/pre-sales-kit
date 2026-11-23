"""服务检查：依赖服务的 HTTP 健康探测。

只依赖标准库 urllib：客户现场经常装不了额外包，也经常没有外网。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Any

from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = (
    "url",
    "method",
    "expect_status",
    "body_contains",
    "headers",
    "timeout",
    "retries",
    "allow_redirects",
)

_MAX_BODY = 64 * 1024


class _Unreachable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@check("service", "HTTP 健康探测：服务是否存活、返回是否符合预期")
def run_service(spec: CheckSpec, ctx: CheckContext) -> list[Item]:  # noqa: ARG001 - ctx 是检查协议的一部分
    params = Params.of(spec, _ALLOWED)
    url = params.text("url", required=True)
    assert url
    method = (params.text("method", "GET") or "GET").upper()
    timeout = params.number("timeout", 3.0, minimum=0.1, maximum=60.0) or 3.0
    retries = params.integer("retries", 0, minimum=0, maximum=5) or 0
    allow_redirects = params.boolean("allow_redirects", True)
    headers = {str(k): str(v) for k, v in params.mapping("headers").items()}
    expect_status = _parse_expect_status(params.raw("expect_status", 200), spec)
    body_contains = _parse_body_contains(params.raw("body_contains"), spec)

    label = _short_label(url)
    last_reason = ""
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(0.3 * attempt, 1.5))
        try:
            status_code, body, elapsed, content_type = _fetch(
                url, method, timeout, headers, allow_redirects
            )
        except _Unreachable as exc:
            last_reason = exc.reason
            continue

        elapsed_text = f"{elapsed * 1000:.0f}ms"
        if status_code not in expect_status:
            expected = " / ".join(str(code) for code in expect_status)
            hint = _status_hint(status_code)
            return [
                Item(
                    label,
                    Status.FAIL,
                    f"返回 HTTP {status_code}（期望 {expected}）· {elapsed_text}",
                    hint=hint,
                )
            ]
        if body_contains:
            text = body.decode("utf-8", errors="replace")
            missing = [needle for needle in body_contains if needle not in text]
            if missing:
                preview = _preview(text)
                return [
                    Item(
                        label,
                        Status.FAIL,
                        f"响应体缺少 {missing[0]!r} · 实际返回：{preview}",
                        hint="服务起来了，但接口内容不对：可能连的是别的版本",
                    )
                ]
        detail = f"HTTP {status_code} · {elapsed_text}"
        if content_type:
            detail += f" · {content_type.split(';')[0].strip()}"
        return [Item(label, Status.PASS, detail)]

    return [
        Item(
            label,
            Status.FAIL,
            f"连不上：{last_reason or '未知原因'}",
            hint="服务未启动 / 端口不对 / 防火墙。可配 fix: start_service 自动拉起",
        )
    ]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """允许「不跟随跳转」：返回 None 会让 urllib 把 3xx 当错误抛出来。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        """固定签名的扩展点：只要返回 None，就表示不跟随跳转。"""
        return


def _fetch(
    url: str,
    method: str,
    timeout: float,
    headers: dict[str, str],
    allow_redirects: bool,
) -> tuple[int, bytes, float, str]:
    request = urllib.request.Request(url, headers=headers, method=method)
    opener = None if allow_redirects else urllib.request.build_opener(_NoRedirect)
    started = time.perf_counter()
    try:
        open_call = opener.open if opener else urllib.request.urlopen
        with open_call(request, timeout=timeout) as response:
            body = response.read(_MAX_BODY) if method != "HEAD" else b""
            elapsed = time.perf_counter() - started
            return int(response.status), body, elapsed, response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read(_MAX_BODY) if method != "HEAD" else b""
        elapsed = time.perf_counter() - started
        return int(exc.code), body or b"", elapsed, exc.headers.get("content-type", "") if exc.headers else ""
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ConnectionRefusedError):
            raise _Unreachable("连接被拒绝（端口没有服务在监听）") from exc
        if isinstance(reason, TimeoutError):
            raise _Unreachable(f"连接超时（{timeout:g}s）") from exc
        if isinstance(reason, OSError) and "getaddrinfo" in str(reason):
            raise _Unreachable("域名解析失败") from exc
        raise _Unreachable(str(reason)) from exc
    except TimeoutError as exc:
        raise _Unreachable(f"读取响应超时（{timeout:g}s）") from exc
    except (OSError, ValueError) as exc:
        raise _Unreachable(str(exc)) from exc


def _parse_expect_status(value: Any, spec: CheckSpec) -> list[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return [value]
    if isinstance(value, list) and value and all(isinstance(code, int) for code in value):
        return [int(code) for code in value]
    raise Params._spec_error(
        spec, "params.expect_status", "应该是整数或整数列表（例如 200 或 [200, 204]）"
    )


def _parse_body_contains(value: Any, spec: CheckSpec) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise Params._spec_error(
        spec, "params.body_contains", "应该是字符串或字符串列表"
    )


def _short_label(url: str) -> str:
    """终端里的标签：去掉 http:// 保留 host 与路径，一眼看出查的是哪个接口。"""
    return url.split("://", 1)[-1].rstrip("/") or url


def _preview(text: str) -> str:
    cleaned = " ".join(text.split())
    return (cleaned[:80] + "…") if len(cleaned) > 80 else cleaned


def _status_hint(code: int) -> str | None:
    if code == 401:
        return "鉴权失败：演示账号的 token / 密码可能过期"
    if code == 403:
        return "被拒绝：检查白名单 / IP 限制"
    if code == 404:
        return "路径不对：确认健康检查地址"
    if code >= 500:
        return "服务在，但内部报错：先看服务日志"
    return None
