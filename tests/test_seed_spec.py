"""点位声明校验。"""

from __future__ import annotations

import pytest

from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.seed.spec import load_seed_spec

_VALID = """
version: 1
name: 测试点位
interval: 10s
points:
  - name: 主轴温度
    unit: ℃
    model: thermal
    baseline: 45
    range: [20, 85]
    decimals: 1
    params:
      noise: 0.05
"""


def test_load_valid(write_yaml) -> None:
    spec = load_seed_spec(write_yaml("points.yaml", _VALID))
    assert spec.name == "测试点位"
    assert spec.interval == 10
    assert spec.points[0].name == "主轴温度"
    assert spec.points[0].range_max == 85
    assert spec.points[0].decimals == 1


def test_unknown_point_field(write_yaml) -> None:
    with pytest.raises(SpecError) as info:
        load_seed_spec(write_yaml("points.yaml", _VALID.replace("    params:", "    parms:")))
    assert "parms" in str(info.value)


def test_unknown_model(write_yaml) -> None:
    with pytest.raises(SpecError) as info:
        load_seed_spec(write_yaml("points.yaml", _VALID.replace("thermal", "magic")))
    assert "未知模型" in str(info.value)


def test_bad_range(write_yaml) -> None:
    with pytest.raises(SpecError):
        load_seed_spec(write_yaml("points.yaml", _VALID.replace("[20, 85]", "[85, 20]")))
    with pytest.raises(SpecError):
        load_seed_spec(write_yaml("points.yaml", _VALID.replace("[20, 85]", "[20]")))


def test_duplicate_names(write_yaml) -> None:
    second = _VALID.split("points:")[1]
    with pytest.raises(SpecError) as info:
        load_seed_spec(write_yaml("points.yaml", _VALID + second))
    assert "重复" in str(info.value)


def test_fault_validation(write_yaml) -> None:
    text = _VALID + """
    faults:
      - kind: 乱来
        from: "10:00"
        to: "11:00"
        magnitude: 0.2
"""
    with pytest.raises(SpecError) as info:
        load_seed_spec(write_yaml("points.yaml", text))
    assert "kind" in str(info.value)


def test_fault_magnitude_bounds(write_yaml) -> None:
    text = _VALID + """
    faults:
      - kind: ramp
        from: "10:00"
        to: "11:00"
        magnitude: 0
"""
    with pytest.raises(SpecError):
        load_seed_spec(write_yaml("points.yaml", text))


def test_empty_points(write_yaml) -> None:
    with pytest.raises(SpecError):
        load_seed_spec(write_yaml("points.yaml", "version: 1\npoints: []\n"))
