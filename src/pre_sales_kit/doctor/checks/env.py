"""环境检查：磁盘、时区、时钟偏差、控制台编码、证书有效期。

这几项单独看都不致命，凑在一起就是「到了现场发现数据时间是错的」。
"""

from __future__ import annotations

import locale
import shutil
import ssl
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen

from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.core.status import Status
from pre_sales_kit.core.text import human_size
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = (
    "disk_path",
    "disk_min_free",
    "timezone",
    "clock_probe_url",
    "clock_max_skew",
    "encoding",
    "cert_path",
    "cert_host",
    "cert_port",
    "cert_min_days",
    "timeout",
)


@check("env", "运行环境：磁盘 / 时区 / 时钟 / 编码 / 证书")
def run_env(spec: CheckSpec, ctx: CheckContext) -> list[Item]:
    params = Params.of(spec, _ALLOWED)
    items: list[Item] = []

    disk_path = params.text("disk_path")
    disk_min_free = params.size("disk_min_free")
    if disk_min_free is not None or disk_path:
        items.append(_check_disk(disk_path, disk_min_free, ctx))

    if params.has("timezone"):
        items.append(_check_timezone(params.text("timezone")))

    if params.has("clock_probe_url"):
        items.append(
            _check_clock(
                params.text("clock_probe_url"),
                params.duration("clock_max_skew", 300.0) or 300.0,
                params.number("timeout", 3.0) or 3.0,
            )
        )

    if params.has("encoding"):
        items.append(_check_encoding(params.text("encoding", "utf-8")))

    cert_path = params.text("cert_path")
    cert_host = params.text("cert_host")
    if cert_path or cert_host:
        items.append(
            _check_certificate(
                cert_path=cert_path,
                host=cert_host,
                port=params.integer("cert_port", 443, minimum=1, maximum=65535) or 443,
                min_days=params.integer("cert_min_days", 14, minimum=0) or 14,
                ctx=ctx,
            )
        )

    if not items:
        raise SpecError(
            message=(
                f"检查「{spec.name}」（{spec.id}）的 env 没有配置任何一项；"
                "至少写 disk_min_free / timezone / encoding / cert_path / clock_probe_url 中的一个"
            ),
            path=spec.source,
        )
    return items


def _check_disk(disk_path: str | None, min_free: int | None, ctx: CheckContext) -> Item:
    label = "磁盘"
    path = Path(disk_path) if disk_path else ctx.base_dir
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return Item(label, Status.FAIL, f"取不到 {path} 的磁盘信息：{exc}")
    free_text = human_size(usage.free)
    location = _display_path(path)
    if min_free is None:
        return Item(label, Status.PASS, f"{location} 剩余 {free_text}")
    if usage.free < min_free:
        return Item(
            label,
            Status.FAIL,
            f"{location} 只剩 {free_text}（要求 ≥ {human_size(min_free)}）",
            hint="演示数据 + 日志很容易把盘吃满；先清理旧报告和数据库",
        )
    return Item(label, Status.PASS, f"{location} 剩余 {free_text}（阈值 {human_size(min_free)}）")


def _check_timezone(expected: str | None) -> Item:
    label = "时区"
    assert expected
    now_local = datetime.now().astimezone()
    local_offset = now_local.utcoffset() or timedelta(0)
    local_name = now_local.tzname() or "未知"
    local_offset_text = _offset_text(local_offset)

    expected_offset, display = _expected_offset(expected)
    if expected_offset is None:
        return Item(
            label,
            Status.WARN,
            f"无法识别时区 {expected!r}：{display}",
            hint="Windows 上 Python 需要 tzdata 包；也可以直接写 utc_offset，例如 +08:00",
        )
    if expected_offset != local_offset:
        return Item(
            label,
            Status.WARN,
            f"本地 {local_offset_text}（{local_name}），与期望 {display} 不一致",
            hint="时区不对会让「今天的数据」判断失真，演示前顺手改掉",
        )
    return Item(label, Status.PASS, f"{local_offset_text} · {local_name}")


def _expected_offset(expected: str) -> tuple[timedelta | None, str]:
    import re

    match = re.fullmatch(r"\s*(?:utc)?\s*([+-])(\d{1,2}):?(\d{2})\s*", expected, re.I)
    if match:
        sign = -1 if match.group(1) == "-" else 1
        offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3))) * sign
        return offset, f"UTC{_offset_text(offset)}"
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(expected)
    except Exception as exc:
        return None, str(exc)
    zone_offset = datetime.now(zone).utcoffset()
    return zone_offset, expected


def _offset_text(offset: timedelta) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _check_clock(probe_url: str | None, max_skew: float, timeout: float) -> Item:
    label = "时钟"
    assert probe_url
    try:
        request = Request(probe_url, method="HEAD")
        with urlopen(request, timeout=timeout) as response:
            date_header = response.headers.get("Date")
    except Exception as exc:
        return Item(
            label,
            Status.SKIP,
            f"无法校验时钟（{probe_url} 不可达：{_short(exc)}）",
            hint="无外网时可忽略；用手机时间核对一下系统时间即可",
        )
    if not date_header:
        return Item(label, Status.SKIP, "探测地址没有返回 Date 头，跳过时钟校验")
    try:
        server_time = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return Item(label, Status.SKIP, f"Date 头无法解析：{date_header}")
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=UTC)
    skew = abs(time.time() - server_time.timestamp())
    if skew > max_skew:
        return Item(
            label,
            Status.FAIL,
            f"系统时间与网络时间相差 {skew:.0f} 秒（阈值 {max_skew:.0f} 秒）",
            hint="时间不对，演示数据的时间戳会全错；先同步系统时间",
        )
    return Item(label, Status.PASS, f"偏差 {skew:.1f} 秒（阈值 {max_skew:.0f} 秒）")


def _check_encoding(expected: str | None) -> Item:
    label = "控制台编码"
    wanted = (expected or "utf-8").lower().replace("-", "").replace("_", "")
    actual = locale.getpreferredencoding(False)
    normalized = actual.lower().replace("-", "").replace("_", "")
    if normalized == wanted:
        return Item(label, Status.PASS, f"{actual} · 中文不会乱码")
    return Item(
        label,
        Status.WARN,
        f"系统首选编码是 {actual}（期望 {expected}）",
        hint="中文可能显示成问号；临时用 chcp 65001，或设置 PYTHONUTF8=1",
    )


def _check_certificate(
    *,
    cert_path: str | None,
    host: str | None,
    port: int,
    min_days: int,
    ctx: CheckContext,
) -> Item:
    label = "证书"
    try:
        if cert_path:
            path = Path(cert_path)
            if not path.is_absolute():
                path = (ctx.base_dir / path).resolve()
            if not path.exists():
                return Item(label, Status.FAIL, f"证书文件不存在：{path}")
            not_after = _cert_file_not_after(path)
            target = path.name
        else:
            assert host
            not_after = _cert_host_not_after(host, port)
            target = f"{host}:{port}"
    except Exception as exc:
        return Item(label, Status.WARN, f"读不出证书信息（{_short(exc)}）", hint="确认路径或地址")

    days = (not_after - datetime.now(UTC)).total_seconds() / 86400
    when = not_after.astimezone().strftime("%Y-%m-%d")
    if days < 0:
        return Item(
            label,
            Status.FAIL,
            f"{target} 已过期 {-days:.0f} 天（{when}）",
            hint="过期证书会让浏览器直接拦下 HTTPS 演示页",
        )
    if days < min_days:
        return Item(
            label,
            Status.FAIL,
            f"{target} 只剩 {days:.0f} 天（{when}，阈值 {min_days} 天）",
            hint="先续期，别等到客户现场",
        )
    return Item(label, Status.PASS, f"{target} 还有 {days:.0f} 天（{when}）")


def _cert_file_not_after(path: Path) -> datetime:
    decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    raw = decoded.get("notAfter")
    if not raw:
        raise ValueError("证书里没有 notAfter")
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(raw), tz=UTC)


def _cert_host_not_after(host: str, port: int) -> datetime:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket_create(host, port, 5.0) as raw, context.wrap_socket(
        raw, server_hostname=host
    ) as tls:
        certificate = tls.getpeercert() or {}
    raw_time = certificate.get("notAfter")
    if not raw_time or not isinstance(raw_time, str):
        raise ValueError("服务端没有提供证书")
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(raw_time), tz=UTC)


def socket_create(host: str, port: int, timeout: float):
    import socket

    return socket.create_connection((host, port), timeout=timeout)


def _display_path(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        return str(path)
    if resolved.drive:
        return resolved.drive + "\\"
    return str(resolved)


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:80]
