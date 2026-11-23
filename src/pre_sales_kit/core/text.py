"""单位与时间的解析 / 格式化。

现场人写的是 30s、2GB、1024x768、今天 14:20，程序要都认。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from pre_sales_kit.core.errors import KitError

_DURATION_RE = re.compile(
    r"^\s*(?P<sign>[+-])?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|sec|m|min|h|hr|d|day)?s?\s*$",
    re.I,
)

_DURATION_UNITS = {
    "ms": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "m": 60.0,
    "min": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
}

_SIZE_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>b|kb|mb|gb|tb|pb)?\s*$", re.I)

_SIZE_UNITS = {
    "b": 1,
    "kb": 1024,
    "mb": 1024**2,
    "gb": 1024**3,
    "tb": 1024**4,
    "pb": 1024**5,
}

_RESOLUTION_RE = re.compile(r"^\s*(?P<w>\d+)\s*[x×*]\s*(?P<h>\d+)\s*$", re.I)


def parse_duration(value: str | int | float) -> float:
    """把 30s / 5m / 2h / 1.5d / 500ms 转成秒。纯数字按秒处理。"""
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_RE.match(str(value))
    if not match:
        raise KitError(f"无法解析时长：{value!r}（示例：30s、5m、2h、1.5d、500ms）")
    unit = (match.group("unit") or "s").lower()
    value = float(match.group("num")) * _DURATION_UNITS[unit]
    return -value if match.group("sign") == "-" else value


def format_duration(seconds: float | None) -> str:
    """人类可读的时长：850ms / 1.2s / 2m05s / 1h03m。"""
    if seconds is None:
        return "—"
    if seconds < 0:
        return "—"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        minutes, rest = divmod(int(seconds), 60)
        return f"{minutes}m{rest:02d}s"
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    return f"{hours}h{minutes:02d}m"


def format_ago(seconds: float | None) -> str:
    """「多久以前」：12 秒前 / 3 分钟前 / 2 小时前。负数视为误差内。"""
    if seconds is None:
        return "未知"
    if seconds < 0:
        return "刚刚"
    if seconds < 60:
        return f"{seconds:.0f} 秒前"
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟前"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{seconds / 86400:.1f} 天前"


def parse_size(value: str | int | float) -> int:
    """把 512MB / 2GB / 1.5TB 转成字节。纯数字按字节。"""
    if isinstance(value, (int, float)):
        return int(value)
    match = _SIZE_RE.match(str(value))
    if not match:
        raise KitError(f"无法解析容量：{value!r}（示例：512MB、2GB、1.5TB）")
    unit = (match.group("unit") or "b").lower()
    return int(float(match.group("num")) * _SIZE_UNITS[unit])


def human_size(num_bytes: int | float | None) -> str:
    """字节转人类可读：836MB / 12.4GB。"""
    if num_bytes is None:
        return "—"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            if unit == "B":
                return f"{value:.0f}B"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def parse_resolution(value: str) -> tuple[int, int]:
    match = _RESOLUTION_RE.match(str(value))
    if not match:
        raise KitError(f"无法解析分辨率：{value!r}（示例：1024x768）")
    return int(match.group("w")), int(match.group("h"))


def parse_count(value: str | int) -> int:
    """支持 1_000、1000、1k、2.5k 的计数阈值。"""
    if isinstance(value, int):
        return value
    text = str(value).strip().replace("_", "").replace(",", "")
    match = re.match(r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>k|m)?$", text, re.I)
    if not match:
        raise KitError(f"无法解析数量：{value!r}（示例：1000、1k、2.5k）")
    multiplier = {"k": 1000, "m": 1_000_000}.get((match.group("unit") or "").lower(), 1)
    return int(float(match.group("num")) * multiplier)


def parse_timestamp(value: object) -> datetime | None:
    """尽量把单元格里的时间认出来：ISO 字符串 / epoch 秒 / 毫秒 / 常见格式。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # 13 位按毫秒，10 位按秒
        if number > 1e11:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number).astimezone()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10}(?:\.\d+)?", text):
        return parse_timestamp(float(text))
    if re.fullmatch(r"\d{13}", text):
        return parse_timestamp(int(text))
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def display_source(path: str | Path, *, base: Path | None = None) -> str:
    """终端里展示路径：在工作目录下就显示相对路径，别让一行被绝对路径撑爆。"""
    from pathlib import Path as _Path

    target = _Path(path)
    anchor = base or _Path.cwd()
    try:
        return str(target.resolve().relative_to(anchor.resolve()))
    except (ValueError, OSError):
        return str(path)


def parse_time_point(value: str, *, now: datetime | None = None) -> datetime:
    """命令行里的时间点：now / -24h / 2026-11-23 14:20 / 14:20（按今天算）。"""
    now = now or datetime.now().astimezone()
    text = value.strip()
    lowered = text.lower()
    if lowered in {"now", "现在"}:
        return now
    if lowered.startswith(("+", "-")) and re.search(r"[smhd]$", lowered):
        return now + timedelta(seconds=parse_duration(lowered))
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        hours, minutes, *secs = (int(part) for part in text.split(":"))
        second = secs[0] if secs else 0
        return now.replace(hour=hours, minute=minutes, second=second, microsecond=0)
    parsed = parse_timestamp(text)
    if parsed is None:
        raise KitError(f"无法解析时间：{value!r}（示例：now、-24h、2026-11-23 14:20、14:20）")
    return parsed


def parse_window(value: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """把 14:20-14:35 解析成一个时间窗（按今天）。"""
    now = now or datetime.now().astimezone()
    text = value.strip()
    parts = re.split(r"\s*(?:-|~|至)\s*", text)
    if len(parts) != 2:
        raise KitError(f"无法解析时间窗：{value!r}（示例：14:20-14:35）")
    start = parse_time_point(parts[0], now=now)
    end = parse_time_point(parts[1], now=now)
    if end <= start:
        end += timedelta(days=1)
    return start, end
