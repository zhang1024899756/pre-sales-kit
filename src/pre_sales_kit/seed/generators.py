"""五种物理模型。

生成的每一条曲线都受两个约束：
1. **量程硬约束**——绝不越界（功率因数不会跑出 0.85~0.95）；
2. **变化率可信**——不会一步跳半个量程（温度不会瞬间跳 50 度）。

随机只用来决定「细节」，曲线的形状由物理（一阶惯性、负载阶跃、均值回归）决定。
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from pre_sales_kit.core.errors import KitError
from pre_sales_kit.seed.models import PointSpec

Generator = Callable[[PointSpec, list[float], float, random.Random, datetime], list[float]]

GENERATORS: dict[str, Generator] = {}
MODEL_DOCS: dict[str, tuple[str, dict[str, str]]] = {}

#: 单步跳变的允许上限（占量程比例）。负载阶跃的启动冲击是物理事实，所以放宽。
JUMP_LIMITS: dict[str, float | None] = {
    "thermal": 0.03,
    "load_step": 0.55,  # 负载阶跃本身就是陡的，这个阈值只拦「离谱」的跳变
    "random_walk": 0.08,
    "triangle": 0.15,
    "counter": None,
}


def model(name: str, description: str, params: dict[str, str]) -> Callable:
    def decorator(func: Generator) -> Generator:
        GENERATORS[name] = func
        MODEL_DOCS[name] = (description, params)
        return func

    return decorator


def generate(
    spec: PointSpec, times: list[datetime], interval: float, rng: random.Random
) -> list[float]:
    try:
        generator = GENERATORS[spec.model]
    except KeyError:
        raise KitError(
            f"点位「{spec.name}」用了未知模型 {spec.model!r}；可用：{'、'.join(GENERATORS)}"
        ) from None
    # 相对秒轴只用来算相位；绝对起点给需要「几点钟」的模型（例如班次计数）
    steps = [index * interval for index in range(len(times))]
    values = generator(spec, steps, interval, rng, times[0] if times else datetime.now())
    if len(values) != len(times):  # pragma: no cover - 防御
        raise KitError(f"模型 {spec.model} 返回的长度不对：{len(values)} ≠ {len(times)}")
    return [clamp(round(value, spec.decimals), spec.range_min, spec.range_max) for value in values]


def _interval_average(interval: float, tau: float) -> float:
    """指数衰减信号在一个采样区间内的平均值 / 初值之比。"""
    if tau <= 0:
        return 1.0
    return (tau / interval) * (1.0 - math.exp(-interval / tau))


def clamp(value: float, low: float, high: float) -> float:
    if math.isnan(value):
        return low
    return max(low, min(high, value))


def _param(spec: PointSpec, key: str, default: float) -> float:
    value = spec.params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KitError(f"点位「{spec.name}」的参数 {key} 必须是数字，实际是 {value!r}")
    return float(value)


@model(
    "thermal",
    "一阶惯性：温度以时间常数追目标温度，升温比降温快，叠加日周期漂移与传感器噪声",
    {
        "tau_heat": "升温时间常数（秒，默认 240）",
        "tau_cool": "降温时间常数（秒，默认 600）",
        "ambient": "环境温度（默认 24）",
        "drift": "目标温度日周期漂移幅度（默认 1.2）",
        "noise": "传感器噪声标准差（默认 0.08）",
    },
)
def thermal(
    spec: PointSpec, steps: list[float], interval: float, rng: random.Random, _start: datetime
) -> list[float]:
    tau_heat = _param(spec, "tau_heat", 240.0)
    tau_cool = _param(spec, "tau_cool", 600.0)
    ambient = _param(spec, "ambient", 24.0)
    drift = _param(spec, "drift", 1.2)
    noise = _param(spec, "noise", 0.08)
    base = spec.baseline if spec.baseline is not None else spec.midpoint

    # 日周期目标温度：两个周期不同的正弦叠加，避免看起来像教科书里的纯正弦
    def target_at(t: float) -> float:
        slow = math.sin(2 * math.pi * t / 86400.0)
        faster = math.sin(2 * math.pi * t / 5400.0 + 0.7)
        return base + drift * (0.7 * slow + 0.3 * faster) + (ambient - base) * 0.02

    value = target_at(0.0) - rng.uniform(0.5, 2.5)
    out: list[float] = []
    for t in steps:
        target = target_at(t)
        tau = tau_heat if target > value else tau_cool
        value += (target - value) * (1.0 - math.exp(-interval / tau))
        value += rng.gauss(0.0, noise)
        out.append(value)
    return out


@model(
    "load_step",
    "负载阶跃：空载 → 若干档负载切换，切换瞬间有启动冲击后指数衰减，叠加白噪声",
    {
        "idle": "空载电流（默认量程 15%）",
        "levels": "负载档位（量程比例列表，默认 [0.25, 0.5, 0.75]）",
        "dwell_min": "每档最短持续时间（秒，默认 45）",
        "dwell_max": "每档最长持续时间（秒，默认 240）",
        "inrush": "启动冲击倍数（相对该档增量，默认 1.35）",
        "inrush_tau": "冲击衰减时间常数（秒，默认 1.5）",
        "noise": "噪声标准差（默认 0.12）",
    },
)
def load_step(
    spec: PointSpec, steps: list[float], interval: float, rng: random.Random, _start: datetime
) -> list[float]:
    levels_raw = spec.params.get("levels", [0.25, 0.5, 0.75])
    if not isinstance(levels_raw, list) or not levels_raw:
        raise KitError(f"点位「{spec.name}」的 levels 必须是非空列表")
    levels = [float(item) for item in levels_raw]
    idle = _param(spec, "idle", spec.range_min + spec.span * 0.15)
    dwell_min = _param(spec, "dwell_min", 45.0)
    dwell_max = _param(spec, "dwell_max", 240.0)
    inrush = _param(spec, "inrush", 1.35)
    inrush_tau = _param(spec, "inrush_tau", 1.5)
    noise = _param(spec, "noise", 0.12)

    def level_value(fraction: float) -> float:
        return idle + (spec.range_max - idle) * fraction

    current_level = level_value(rng.choice(levels))
    dwell_left = rng.uniform(dwell_min, dwell_max)
    burst = 0.0
    out: list[float] = []
    for _t in steps:
        dwell_left -= interval
        if dwell_left <= 0:
            previous = current_level
            current_level = level_value(rng.choice(levels))
            dwell_left = rng.uniform(dwell_min, dwell_max)
            # 启动冲击：相对增量的倍数，随后按时间常数衰减
            delta = max(0.0, current_level - idle)
            burst = delta * (inrush - 1.0) + max(0.0, current_level - previous) * 0.2
        # 采样器看到的是「这个采样区间内的平均电流」：冲击只有 1.5s，
        # 5s 采一次就只取到一个被摊薄的小鼓包——这才是真实仪表的样子
        visible_burst = burst * _interval_average(interval, inrush_tau)
        value = current_level + visible_burst + rng.gauss(0.0, noise)
        burst *= math.exp(-interval / inrush_tau)
        out.append(value)
    return out


@model(
    "random_walk",
    "有界随机游走（均值回归）：像真实仪表读数一样在小范围内游走，绝不越界",
    {
        "start": "初始值（默认量程中点）",
        "theta": "回归强度（默认 0.35，越大越黏中值）",
        "sigma": "每步波动强度（默认量程的 0.12%）",
        "noise": "额外白噪声（默认量程的 0.1%）",
    },
)
def random_walk(
    spec: PointSpec,
    steps: list[float],
    interval: float,
    rng: random.Random,
    _start: datetime,
) -> list[float]:
    value = _param(spec, "start", spec.midpoint)
    theta = _param(spec, "theta", 0.35)
    sigma = _param(spec, "sigma", spec.span * 0.0012)
    noise = _param(spec, "noise", spec.span * 0.0002)
    mu = spec.midpoint
    out: list[float] = []
    for _t in steps:
        # Ornstein–Uhlenbeck 离散化：向均值回归 + 随机扰动
        value += theta * (mu - value) * interval / 60.0 + rng.gauss(0.0, sigma) * math.sqrt(interval)
        # 触边反弹，而不是硬夹在边界上（硬夹会形成一条假的直线）
        if value < spec.range_min or value > spec.range_max:
            over = value - spec.range_max if value > spec.range_max else value - spec.range_min
            value -= 2.0 * over
        value += rng.gauss(0.0, noise)
        out.append(clamp(value, spec.range_min, spec.range_max))
    return out


@model(
    "triangle",
    "三角波：液位 / 压力这类「进料-出料」节奏，填充与排空速率不同",
    {
        "period": "一个完整循环的秒数（默认 900）",
        "duty": "填充占空比（默认 0.55）",
        "low": "循环下限（默认量程 10%）",
        "high": "循环上限（默认量程 90%）",
        "noise": "噪声标准差（默认量程的 0.4%）",
    },
)
def triangle(
    spec: PointSpec, steps: list[float], _interval: float, rng: random.Random, _start: datetime
) -> list[float]:
    period = _param(spec, "period", 900.0)
    duty = min(0.95, max(0.05, _param(spec, "duty", 0.55)))
    low = _param(spec, "low", spec.range_min + spec.span * 0.1)
    high = _param(spec, "high", spec.range_min + spec.span * 0.9)
    noise = _param(spec, "noise", spec.span * 0.004)
    if high <= low:
        high = low + spec.span * 0.2
    rise = period * duty
    fall = period - rise
    out: list[float] = []
    for t in steps:
        phase = t % period
        if phase <= rise:
            value = low + (high - low) * (phase / rise)
        else:
            value = high - (high - low) * ((phase - rise) / fall)
        out.append(value + rng.gauss(0.0, noise))
    return out


@model(
    "counter",
    "产量计数：单调递增，按节拍出件，偶发停机；可设班次重置（08:00/20:00）",
    {
        "rate": "节拍（件/分钟，默认 12）",
        "downtime_per_hour": "每小时期望停机次数（默认 0.4）",
        "downtime_seconds": "每次停机时长（秒，默认 90）",
        "reset_hours": "班次重置小时列表（默认 [8, 20]；写 [] 表示不重置）",
    },
)
def counter(
    spec: PointSpec, steps: list[float], interval: float, rng: random.Random, start: datetime
) -> list[float]:
    rate = _param(spec, "rate", 12.0)
    downtime_per_hour = _param(spec, "downtime_per_hour", 0.4)
    downtime_seconds = _param(spec, "downtime_seconds", 90.0)
    reset_hours_raw = spec.params.get("reset_hours", [8, 20])
    reset_hours = {int(hour) for hour in reset_hours_raw} if isinstance(reset_hours_raw, list) else set()

    initial = spec.params.get("start", spec.range_min)
    total = float(initial) if isinstance(initial, (int, float)) else spec.range_min
    downtime_left = 0.0
    last_hour = start.hour
    out: list[float] = []
    for t in steps:
        # 班次重置按真实钟点：数据集可能从 17:00 开始，重置点仍然是 20:00
        hour = (start + timedelta(seconds=t)).hour
        if hour in reset_hours and hour != last_hour:
            total = 0.0
        last_hour = hour

        if downtime_left > 0:
            downtime_left -= interval
        else:
            chance = downtime_per_hour * interval / 3600.0
            if rng.random() < chance:
                downtime_left = downtime_seconds
        if downtime_left > 0:
            out.append(total)
            continue

        expected = rate * interval / 60.0
        whole = int(expected)
        fractional = expected - whole
        produced = whole + (1 if rng.random() < fractional else 0)
        total += produced
        out.append(total)
    # 计数类不允许回落：班次重置是唯一例外，其余一律修成单调
    fixed: list[float] = []
    previous = 0.0
    for index, value in enumerate(out):
        hour_prev = (start + timedelta(seconds=steps[index - 1])).hour if index else last_hour
        hour_now = (start + timedelta(seconds=steps[index])).hour
        if index and hour_now in reset_hours and hour_now != hour_prev and value < previous:
            fixed.append(value)
            previous = value
            continue
        if value < previous:
            value = previous
        fixed.append(value)
        previous = value
    return fixed
