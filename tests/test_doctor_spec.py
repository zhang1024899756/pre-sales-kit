"""声明文件校验：宁可报错，也不能让拼错的检查被静默跳过。"""

from __future__ import annotations

import pytest

from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.doctor.spec import load_spec

_VALID = """
version: 1
title: 现场自检
name: 测试环境
checks:
  - id: portal
    name: 演示门户端口
    type: port
    params:
      port: 8080
  - name: 磁盘
    type: env
    params:
      disk_min_free: 512MB
    tags: [环境]
defaults:
  timeout: 10
"""


def test_load_valid_spec(write_yaml) -> None:
    spec = load_spec(write_yaml("doctor.yaml", _VALID))
    assert spec.title == "现场自检"
    assert spec.name == "测试环境"
    assert [item.id for item in spec.checks] == ["portal", "env-2"]
    assert spec.checks[1].tags == ("环境",)
    assert spec.checks[0].timeout == 10


def test_unknown_top_level_field(write_yaml) -> None:
    with pytest.raises(SpecError) as info:
        load_spec(write_yaml("doctor.yaml", "checks: []\nchekcs: []\n"))
    assert "未知字段" in str(info.value)


def test_unknown_check_field(write_yaml) -> None:
    text = _VALID.replace("    params:", "    parms:")
    with pytest.raises(SpecError) as info:
        load_spec(write_yaml("doctor.yaml", text))
    assert "parms" in str(info.value)


def test_duplicate_ids(write_yaml) -> None:
    text = """
checks:
  - id: same
    name: A
    type: port
    params: {port: 1}
  - id: same
    name: B
    type: port
    params: {port: 2}
"""
    with pytest.raises(SpecError) as info:
        load_spec(write_yaml("doctor.yaml", text))
    assert "重复" in str(info.value)


def test_unknown_check_type_lists_available(write_yaml) -> None:
    text = """
checks:
  - name: 神秘检查
    type: magic
"""
    with pytest.raises(SpecError) as info:
        load_spec(write_yaml("doctor.yaml", text))
    assert "port" in str(info.value)


def test_all_checks_disabled(write_yaml) -> None:
    text = """
checks:
  - name: A
    type: port
    params: {port: 1}
    enabled: false
"""
    with pytest.raises(SpecError):
        load_spec(write_yaml("doctor.yaml", text))


def test_fix_requires_command(write_yaml) -> None:
    text = """
checks:
  - name: A
    type: port
    params: {port: 1}
    fix:
      action: start_service
"""
    with pytest.raises(SpecError) as info:
        load_spec(write_yaml("doctor.yaml", text))
    assert "command" in str(info.value)


def test_fix_unknown_action(write_yaml) -> None:
    text = """
checks:
  - name: A
    type: port
    params: {port: 1}
    fix:
      action: 重启一切
"""
    with pytest.raises(SpecError):
        load_spec(write_yaml("doctor.yaml", text))


def test_missing_file() -> None:
    from pre_sales_kit.core.errors import KitError

    with pytest.raises(KitError):
        load_spec("不存在的文件.yaml")
