"""故障注入：在指定时段制造「看起来像真的」的异常。

重点是：注入之后数据仍然物理上说得通。电流爬升 23% 是异常，
一秒跳 40 安不是异常，是假数据。
"""

from __future__ import annotations

import math
import random
from datetime import datetime

from pre_sales_kit.seed.generators import clamp
from pre_sales_kit.seed.models import FaultSpec, PointSpec

FAULT_KINDS = ("ramp", "spike", "dropout", "drift")

_FAULT_LABELS = {
    "ramp": "缓升后恢复",
    "spike": "尖峰冲击",
    "dropout": "传感器卡死",
    "drift": "漂移不恢复",
}


def fault_label(kind: str) -> str:
    return _FAULT_LABELS.get(kind, kind)


def apply_faults(
    values: list[float],
    times: list[datetime],
    spec: PointSpec,
    rng: random.Random,
) -> list[FaultSpec]:
    """原地修改 values，返回真正生效的故障段。"""
    applied: list[FaultSpec] = []
    if not spec.faults:
        return applied
    for fault in spec.faults:
        indices = [index for index, moment in enumerate(times) if fault.start <= moment <= fault.end]
        if len(indices) < 2:
            continue
        delta = fault.magnitude * spec.span
        first, last = indices[0], indices[-1]
        if fault.kind == "ramp":
            for index in indices:
                progress = (index - first) / max(1, last - first)
                values[index] += delta * _ramp_shape(progress)
        elif fault.kind == "spike":
            centers = sorted(rng.uniform(0.15, 0.85) for _ in range(rng.randint(1, 3)))
            sigma = max(1.0, fault.seconds / 40.0)
            for index in indices:
                elapsed = (times[index] - fault.start).total_seconds()
                bump = sum(
                    math.exp(-((elapsed - center * fault.seconds) ** 2) / (2 * sigma**2))
                    for center in centers
                )
                values[index] += delta * min(1.6, bump)
        elif fault.kind == "dropout":
            frozen = values[first]
            for index in indices:
                values[index] = frozen
        elif fault.kind == "drift":
            for index in range(first, len(values)):
                progress = (times[index] - fault.start).total_seconds() / fault.seconds
                values[index] += delta * min(1.0, max(0.0, progress))
        else:  # pragma: no cover - 已在加载时校验
            continue
        applied.append(fault)

    for index in range(len(values)):
        values[index] = clamp(round(values[index], spec.decimals), spec.range_min, spec.range_max)
    return applied


def _ramp_shape(progress: float) -> float:
    """升 25% → 平台 → 降 25%，两端做平滑，避免出现直角拐点。"""
    if progress < 0.25:
        return _smoothstep(progress / 0.25)
    if progress > 0.75:
        return _smoothstep((1.0 - progress) / 0.25)
    return 1.0


def _smoothstep(value: float) -> float:
    clamped = min(1.0, max(0.0, value))
    return clamped * clamped * (3.0 - 2.0 * clamped)


def align_into_window(fault: FaultSpec, first: datetime, last: datetime) -> FaultSpec:
    """把「只写了时分」的故障段挪到数据窗内的那一天。

    例如数据窗是「昨天 18:00 → 今天 18:00」，故障写的是「14:20-14:35」：
    对齐到今天才在窗内，对齐到昨天就已经在窗之外了。逐个试前后几天，取第一个落进来的。
    """
    if not fault.time_of_day:
        return fault
    from datetime import timedelta

    for shift_days in (0, -1, 1, -2, 2):
        shift = timedelta(days=shift_days)
        candidate = FaultSpec(
            kind=fault.kind,
            start=fault.start + shift,
            end=fault.end + shift,
            magnitude=fault.magnitude,
            note=fault.note,
            time_of_day=True,
        )
        if candidate.start <= last and candidate.end >= first:
            return candidate
    return fault


def align_fault(fault: FaultSpec, anchor: datetime) -> FaultSpec:
    """把「今天 14:20」对齐到数据集所在的那一天（差几天就平移几天）。

    只对「只写了时分」的窗口生效；写了完整日期的故障段保持原样，
    否则「十天前的异常」会被平移到今天，凭空多出一段故障。
    """
    if not fault.time_of_day:
        return fault
    days = (anchor.date() - fault.start.date()).days
    if days == 0:
        return fault
    from datetime import timedelta

    shift = timedelta(days=days)
    return FaultSpec(
        kind=fault.kind,
        start=fault.start + shift,
        end=fault.end + shift,
        magnitude=fault.magnitude,
        note=fault.note,
        time_of_day=True,
    )
