"""seed 的数据模型：点位声明、故障声明、生成结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class FaultSpec:
    """一段注入的异常：用来配合告警演示。"""

    kind: str  # ramp | spike | dropout | drift
    start: datetime
    end: datetime
    magnitude: float  # 相对量程的比例，例如 0.23 = 23%
    note: str | None = None
    #: 只写了「14:20」这种时分（没写日期）时为 True，生成时跟随数据集所在日期
    time_of_day: bool = False

    @property
    def seconds(self) -> float:
        return max(1.0, (self.end - self.start).total_seconds())


@dataclass(frozen=True)
class PointSpec:
    """一个演示点位。range 是硬约束：任何时刻都不越界。"""

    name: str
    model: str
    range_min: float
    range_max: float
    unit: str = ""
    baseline: float | None = None
    decimals: int = 2
    params: dict[str, Any] = field(default_factory=dict)
    faults: tuple[FaultSpec, ...] = ()

    @property
    def span(self) -> float:
        return self.range_max - self.range_min

    @property
    def midpoint(self) -> float:
        return (self.range_min + self.range_max) / 2


@dataclass(frozen=True)
class SeedSpec:
    name: str
    source: str
    points: tuple[PointSpec, ...]
    interval: float
    description: str | None = None


@dataclass
class PointSeries:
    """一个点位生成出来的一列时间序列。"""

    spec: PointSpec
    times: list[datetime]
    values: list[float]
    faults: list[FaultSpec] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.values)


@dataclass
class Dataset:
    """整份演示数据。"""

    series: list[PointSeries]
    times: list[datetime]
    interval: float
    seed: int
    source: str
    started_at: datetime
    finished_at: datetime
    title: str | None = None

    @property
    def total_rows(self) -> int:
        return sum(len(item) for item in self.series)

    @property
    def point_count(self) -> int:
        return len(self.series)


@dataclass
class PointStats:
    """自检结果：证明生成的数据没有物理硬伤。"""

    name: str
    model: str
    unit: str
    count: int
    minimum: float
    maximum: float
    max_step: float
    out_of_range: int
    non_monotonic: int
    flat_run: int
    range_span: float = 0.0

    @property
    def span(self) -> float:
        """实际数据的波动幅度（只用来看「这列数据有没有变化」）。"""
        return self.maximum - self.minimum

    def problems(self, *, jump_ratio: float = 0.08) -> list[str]:
        issues: list[str] = []
        if self.out_of_range:
            issues.append(f"{self.out_of_range} 个点越过量程")
        if self.non_monotonic:
            issues.append(f"{self.non_monotonic} 次回落（计数类不允许）")
        # 跳变率按「声明的量程」算，而不是按实际波动幅度：
        # 否则一条平稳的曲线也会因为 0.3 的小抖动被判成大幅跳变
        span = self.range_span or self.span
        if self.model != "counter" and span > 0 and self.max_step / span > jump_ratio:
            issues.append(f"单步跳变量程的 {self.max_step / span:.0%}")
        # 连续不变太长，通常不是「数据平稳」，而是 decimals 太粗把变化四舍五入没了
        if self.model != "counter" and self.count and self.flat_run > max(30, self.count * 0.3):
            issues.append(
                f"连续 {self.flat_run} 个采样完全不变（像一条直线，考虑调大 decimals）"
            )
        return issues
