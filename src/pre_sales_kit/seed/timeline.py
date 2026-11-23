"""时间轴：把「最近 24 小时、5 秒一个点」变成具体的时间戳列表。"""

from __future__ import annotations

from datetime import datetime, timedelta

from pre_sales_kit.core.errors import KitError
from pre_sales_kit.core.text import format_duration

#: 单次生成的上限，防止手滑写 --interval 1ms 把内存打爆
MAX_SAMPLES = 2_000_000


def build_times(start: datetime, end: datetime, interval: float) -> list[datetime]:
    if interval <= 0:
        raise KitError(f"采样间隔必须大于 0，实际是 {interval}")
    if end <= start:
        raise KitError(
            f"结束时间必须晚于开始时间（{start.isoformat()} → {end.isoformat()}）",
            hint="检查 --start / --end 的写法，例如 --start -24h --end now",
        )
    total = (end - start).total_seconds()
    count = int(total // interval) + 1
    if count > MAX_SAMPLES:
        raise KitError(
            f"按 {interval:g}s 间隔要生成 {count:,} 个点，超过上限 {MAX_SAMPLES:,}",
            hint=f"把间隔调大，或把时间窗缩短（当前 {format_duration(total)}）",
        )
    if count < 2:
        raise KitError("时间窗太短，至少要有两个采样点")
    step = timedelta(seconds=interval)
    return [start + step * index for index in range(count)]
