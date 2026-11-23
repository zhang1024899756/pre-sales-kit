"""数据预览 HTML：有曲线、有故障段、离线可用。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from pre_sales_kit.seed.models import FaultSpec
from pre_sales_kit.seed.preview import render_preview, sparkline, write_preview
from pre_sales_kit.seed.service import build_run
from pre_sales_kit.seed.spec import load_seed_spec

_SPEC = """
version: 1
name: 预览测试
interval: 60s
points:
  - name: 主轴温度
    unit: ℃
    model: thermal
    baseline: 45
    range: [20, 85]
    decimals: 1
  - name: 功率因数
    model: random_walk
    range: [0.85, 0.95]
    decimals: 4
"""


@pytest.fixture
def run(write_yaml):
    spec = load_seed_spec(write_yaml("points.yaml", _SPEC))
    end = datetime.now().astimezone()
    start = end - timedelta(hours=2)
    fault = FaultSpec(
        kind="ramp",
        start=start + timedelta(minutes=30),
        end=start + timedelta(minutes=50),
        magnitude=0.2,
        time_of_day=True,
    )
    return build_run(
        spec, start=start, end=end, interval=60, seed=5, extra_faults={"主轴温度": [fault]}
    )


def test_sparkline_shape() -> None:
    times = [datetime.now().astimezone() + timedelta(seconds=60 * index) for index in range(10)]
    chart = sparkline([1.0, 2.0, 1.5, 3.0, 2.2, 2.8, 1.9, 2.4, 2.0, 2.6], times)
    assert chart["path"].startswith("M")
    assert chart["envelope"].endswith("Z")  # min/max 包络
    assert chart["low"] < 1.0 and chart["high"] > 3.0


def test_sparkline_keeps_peaks_with_few_buckets() -> None:
    """抽稀不能把尖峰抹掉：包络把最大值保留在曲线上。"""
    times = [datetime.now().astimezone() + timedelta(seconds=index) for index in range(1000)]
    values = [10.0] * 1000
    values[500] = 99.0
    chart = sparkline(values, times)
    assert chart["high"] > 99.0


def test_render_contains_points_and_svg(run) -> None:
    html = render_preview(run)
    assert "预览测试" in html
    assert "主轴温度" in html
    assert "功率因数" in html
    assert "<svg" in html
    assert "缓升后恢复" in html  # 故障段标注
    assert "物理自检通过" in html


def test_preview_is_offline(run) -> None:
    html = render_preview(run)
    assert "<script" not in html
    assert not re.search(r"https?://", html)


def test_write_preview(run, tmp_path) -> None:
    path = write_preview(run, tmp_path / "preview.html")
    assert path.exists()
    assert path.stat().st_size > 2000
