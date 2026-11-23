"""生成 + 自检 + 故障注入的整条链路。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pre_sales_kit.core.errors import KitError
from pre_sales_kit.seed.faults import FAULT_KINDS, apply_faults, fault_label
from pre_sales_kit.seed.models import FaultSpec, PointSpec
from pre_sales_kit.seed.service import build_run, validate_fault_points, verify
from pre_sales_kit.seed.spec import load_seed_spec

_SPEC = """
version: 1
name: 测试
interval: 30s
points:
  - name: 温度
    unit: ℃
    model: thermal
    baseline: 45
    range: [20, 85]
    decimals: 1
  - name: 电流
    unit: A
    model: load_step
    range: [0, 32]
  - name: 产量
    unit: 件
    model: counter
    range: [0, 6000]
    params:
      rate: 6
      reset_hours: []
"""


def _spec(write_yaml):
    return load_seed_spec(write_yaml("points.yaml", _SPEC))


def test_build_run_shape_and_verify(write_yaml) -> None:
    spec = _spec(write_yaml)
    end = datetime.now().astimezone()
    run = build_run(spec, start=end - timedelta(hours=2), end=end, interval=30, seed=1)
    assert run.dataset.point_count == 3
    assert len(run.dataset.times) == 241
    assert run.dataset.total_rows == 723
    assert not run.failed_points, [stat.problems() for stat in run.failed_points]
    assert all(not stat.out_of_range for stat in run.stats)


def test_same_seed_same_data(write_yaml) -> None:
    spec = _spec(write_yaml)
    end = datetime.now().astimezone()
    first = build_run(spec, start=end - timedelta(hours=1), end=end, seed=7)
    second = build_run(spec, start=end - timedelta(hours=1), end=end, seed=7)
    other = build_run(spec, start=end - timedelta(hours=1), end=end, seed=8)
    assert [s.values for s in first.dataset.series] == [s.values for s in second.dataset.series]
    assert [s.values for s in first.dataset.series] != [s.values for s in other.dataset.series]


def test_fault_changes_data_inside_window(write_yaml) -> None:
    spec = _spec(write_yaml)
    end = datetime.now().astimezone()
    start = end - timedelta(hours=2)
    plain = build_run(spec, start=start, end=end, seed=3)
    fault = FaultSpec(kind="ramp", start=start + timedelta(minutes=30), end=start + timedelta(minutes=60), magnitude=0.3)
    with_fault = build_run(
        spec,
        start=start,
        end=end,
        seed=3,
        extra_faults={"电流": [fault]},
    )
    assert "电流" in with_fault.applied
    plain_values = next(s for s in plain.dataset.series if s.spec.name == "电流").values
    fault_values = next(s for s in with_fault.dataset.series if s.spec.name == "电流").values
    assert sum(fault_values) > sum(plain_values)  # 缓升确实把电流抬上去了
    assert len(fault_values) == len(plain_values)


def test_fault_outside_window_is_skipped(write_yaml) -> None:
    spec = _spec(write_yaml)
    end = datetime.now().astimezone()
    start = end - timedelta(hours=1)
    stale = FaultSpec(kind="ramp", start=start - timedelta(days=10), end=start - timedelta(days=10, hours=-1), magnitude=0.2)
    run = build_run(spec, start=start, end=end, extra_faults={"温度": [stale]})
    assert "温度" not in run.applied


def test_unknown_fault_point_raises(write_yaml) -> None:
    spec = _spec(write_yaml)
    with pytest.raises(KitError) as info:
        validate_fault_points(spec, {"不存在": []})
    assert "不存在" in str(info.value)


def test_fault_kinds_have_labels() -> None:
    for kind in FAULT_KINDS:
        assert fault_label(kind)


def test_verify_detects_out_of_range() -> None:
    """verify 是最后一道闸：数据越界必须被抓出来。"""
    from pre_sales_kit.seed.models import Dataset, PointSeries

    times = [datetime.now().astimezone() + timedelta(seconds=30 * i) for i in range(5)]
    spec = PointSpec("异常点位", "thermal", 20, 85, decimals=0)
    series = PointSeries(spec=spec, times=times, values=[30.0, 30.0, 200.0, 30.0, 30.0])
    dataset = Dataset(
        series=[series],
        times=times,
        interval=30,
        seed=1,
        source="test.yaml",
        started_at=times[0],
        finished_at=times[-1],
    )
    stats = verify(dataset)
    assert stats[0].out_of_range == 1
    assert stats[0].problems()


def test_apply_faults_returns_effective_segments() -> None:
    # 这里只测 apply_faults 本身，不需要加载声明文件
    end = datetime.now().astimezone()
    start = end - timedelta(hours=1)
    times = [start + timedelta(minutes=index) for index in range(60)]
    point = PointSpec("温度", "thermal", 20, 85, params={}, decimals=1)
    fault = FaultSpec(kind="spike", start=start + timedelta(minutes=20), end=start + timedelta(minutes=30), magnitude=0.5)
    point = PointSpec(
        "温度", "thermal", 20, 85, decimals=1, faults=(fault,)
    )
    import random

    values = [45.0] * len(times)
    applied = apply_faults(values, times, point, random.Random(1))
    assert applied and applied[0].kind == "spike"
    assert max(values) > 45.0
