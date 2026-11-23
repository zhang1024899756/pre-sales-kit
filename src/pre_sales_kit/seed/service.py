"""生成 + 自检：把声明变成数据，并证明数据没有物理硬伤。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime

from pre_sales_kit.core.errors import KitError
from pre_sales_kit.seed import generators
from pre_sales_kit.seed.faults import align_into_window, apply_faults
from pre_sales_kit.seed.models import Dataset, FaultSpec, PointSeries, PointSpec, PointStats
from pre_sales_kit.seed.timeline import build_times


@dataclass
class SeedRun:
    dataset: Dataset
    stats: list[PointStats] = field(default_factory=list)
    applied: dict[str, list[FaultSpec]] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for stat in self.stats if not stat.problems(jump_ratio=_jump_limit(stat.model)))

    @property
    def failed_points(self) -> list[PointStats]:
        return [stat for stat in self.stats if stat.problems(jump_ratio=_jump_limit(stat.model))]


def _jump_limit(model: str) -> float:
    limit = generators.JUMP_LIMITS.get(model, 0.08)
    return limit if limit is not None else 100.0


def build_run(
    spec,
    *,
    start: datetime,
    end: datetime,
    interval: float | None = None,
    seed: int = 42,
    extra_faults: dict[str, list[FaultSpec]] | None = None,
) -> SeedRun:
    step = float(interval or spec.interval)
    times = build_times(start, end, step)
    extra = extra_faults or {}

    series_list: list[PointSeries] = []
    applied: dict[str, list[FaultSpec]] = {}
    for point in spec.points:
        faults = tuple(
            align_into_window(fault, times[0], times[-1]) for fault in point.faults
        ) + tuple(
            align_into_window(fault, times[0], times[-1])
            for fault in extra.get(point.name, ())
        )
        effective = PointSpec(
            name=point.name,
            model=point.model,
            range_min=point.range_min,
            range_max=point.range_max,
            unit=point.unit,
            baseline=point.baseline,
            decimals=point.decimals,
            params=point.params,
            faults=faults,
        )
        # 每个点位独立的随机流：加点、改顺序都不会影响其它点位的曲线
        rng = random.Random(f"{seed}:{point.name}")
        values = generators.generate(effective, times, step, rng)
        used = apply_faults(values, times, effective, rng) if faults else []
        if used:
            applied[point.name] = used
        series_list.append(PointSeries(spec=effective, times=times, values=values, faults=list(used)))

    now = datetime.now().astimezone()
    dataset = Dataset(
        series=series_list,
        times=times,
        interval=step,
        seed=seed,
        source=spec.source,
        started_at=times[0],
        finished_at=now,
        title=spec.name,
    )
    run = SeedRun(dataset=dataset, applied=applied)
    run.stats = verify(dataset)
    return run


def verify(dataset: Dataset) -> list[PointStats]:
    stats: list[PointStats] = []
    for series in dataset.series:
        spec = series.spec
        values = series.values
        out_of_range = sum(1 for value in values if value < spec.range_min or value > spec.range_max)
        reset_hours = spec.params.get("reset_hours", [8, 20]) if spec.model == "counter" else []
        resets = {int(hour) for hour in reset_hours} if isinstance(reset_hours, list) else set()

        def is_shift_reset(index: int, times: list, shifts: set[int]) -> bool:
            if not shifts:
                return False
            hour_before = times[index - 1].hour
            hour_now = times[index].hour
            return hour_now in shifts and hour_now != hour_before

        max_step = 0.0
        non_monotonic = 0
        for index in range(1, len(values)):
            if is_shift_reset(index, series.times, resets):
                # 班次重置是对的，不算跳变、不算回落
                continue
            max_step = max(max_step, abs(values[index] - values[index - 1]))
            # 只有计数类才要求单调；温度、电流本来就该上下波动
            if spec.model == "counter" and values[index] < values[index - 1]:
                non_monotonic += 1
        flat_run = 0
        current_run = 1
        for index in range(1, len(values)):
            if values[index] == values[index - 1]:
                current_run += 1
                flat_run = max(flat_run, current_run)
            else:
                current_run = 1
        stats.append(
            PointStats(
                name=spec.name,
                model=spec.model,
                unit=spec.unit,
                count=len(values),
                minimum=min(values) if values else 0.0,
                maximum=max(values) if values else 0.0,
                max_step=max_step,
                out_of_range=out_of_range,
                non_monotonic=non_monotonic,
                flat_run=flat_run,
                range_span=spec.span,
            )
        )
    return stats


def validate_fault_points(spec, extra: dict[str, list[FaultSpec]]) -> None:
    """校验 --inject-fault 点名的点位确实存在。"""
    missing = [name for name in extra if name not in {point.name for point in spec.points}]
    if missing:
        known = "、".join(point.name for point in spec.points)
        raise KitError(
            f"--fault-point 指定的点位不存在：{'、'.join(missing)}",
            hint=f"可用点位：{known}",
        )
