"""五种物理模型：越界为零、形状可信、可复现。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from itertools import pairwise

import pytest

from pre_sales_kit.seed.generators import GENERATORS, generate
from pre_sales_kit.seed.models import PointSpec
from pre_sales_kit.seed.timeline import build_times

_POINTS = {
    "thermal": PointSpec("温度", "thermal", 20, 85, unit="℃", baseline=45, params={"noise": 0.05}),
    "load_step": PointSpec("电流", "load_step", 0, 32, unit="A", params={"levels": [0.2, 0.5]}),
    "random_walk": PointSpec("功率因数", "random_walk", 0.85, 0.95, decimals=4),
    "triangle": PointSpec("液位", "triangle", 0, 100, unit="%"),
    "counter": PointSpec("产量", "counter", 0, 6000, unit="件", params={"rate": 6}),
}


@pytest.fixture(scope="module")
def times():
    end = datetime(2026, 11, 23, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)
    return build_times(end - timedelta(hours=1), end, 5)


@pytest.mark.parametrize("model", list(_POINTS))
def test_generator_stays_in_range(model, times) -> None:
    spec = _POINTS[model]
    values = generate(spec, times, 5, random.Random(7))
    assert len(values) == len(times)
    assert all(spec.range_min <= value <= spec.range_max for value in values)


@pytest.mark.parametrize("model", list(_POINTS))
def test_generator_is_deterministic(model, times) -> None:
    spec = _POINTS[model]
    first = generate(spec, times, 5, random.Random(11))
    second = generate(spec, times, 5, random.Random(11))
    third = generate(spec, times, 5, random.Random(12))
    assert first == second
    assert first != third


def test_thermal_changes_slowly(times) -> None:
    spec = _POINTS["thermal"]
    values = generate(spec, times, 5, random.Random(3))
    span = spec.range_max - spec.range_min
    largest = max(abs(b - a) for a, b in pairwise(values))
    assert largest / span < 0.02  # 5 秒内不可能跳 2% 量程


def test_random_walk_actually_moves(times) -> None:
    spec = _POINTS["random_walk"]
    values = generate(spec, times, 5, random.Random(5))
    assert max(values) - min(values) > 0.002  # 一小时窗口内也要有可见变化


def test_counter_never_decreases(times) -> None:
    spec = _POINTS["counter"]
    values = generate(spec, times, 5, random.Random(9))
    assert all(b >= a for a, b in pairwise(values))


def test_too_coarse_decimals_are_flagged() -> None:
    """0.85~0.95 只保留两位小数 → 数据会变成一条 0.90 的直线，自检要能发现。"""
    from pre_sales_kit.seed.models import PointStats

    stat = PointStats(
        name="功率因数",
        model="random_walk",
        unit="",
        count=100,
        minimum=0.9,
        maximum=0.9,
        max_step=0.0,
        out_of_range=0,
        non_monotonic=0,
        flat_run=100,
        range_span=0.1,
    )
    assert any("直线" in issue for issue in stat.problems())


def test_counter_resets_at_shift_boundary() -> None:
    tz = datetime.now().astimezone().tzinfo
    start = datetime(2026, 11, 23, 7, 55, tzinfo=tz)
    times = build_times(start, start + timedelta(minutes=15), 60)
    spec = PointSpec("产量", "counter", 0, 5000, params={"rate": 60, "reset_hours": [8]})
    values = generate(spec, times, 60, random.Random(1))
    drops = [index for index in range(1, len(values)) if values[index] < values[index - 1]]
    assert drops, "8 点班次应当出现一次清零"
    assert all(times[index].hour == 8 for index in drops)


def test_unknown_model_is_reported(times) -> None:
    from pre_sales_kit.core.errors import KitError

    with pytest.raises(KitError) as info:
        generate(PointSpec("怪", "magic", 0, 1), times, 5, random.Random(1))
    assert "未知模型" in str(info.value)


def test_every_registered_model_is_covered() -> None:
    assert set(_POINTS) == set(GENERATORS)
