"""兼容性矩阵：加载、匹配、组合判定、三种输出。"""

from __future__ import annotations

import re

import pytest

from pre_sales_kit.compat.models import CompatItem, Dimension
from pre_sales_kit.compat.query import describe, evaluate
from pre_sales_kit.compat.render import render_html, render_markdown, verdict_json
from pre_sales_kit.compat.spec import load_matrix
from pre_sales_kit.core.errors import SpecError

_MATRIX = """
version: 1
product: 测试平台 v1.0
updated: "2025-11-01"
notes: 组合未列出的，按最差维度判断。
os:
  - name: Windows Server 2016
    status: partial
    note: 需补丁 KB4532945
  - name: Windows Server 2019
    status: full
  - name: CentOS 7.9
    status: 部分支持
    note: glibc 版本要求
db:
  - name: Oracle 11g
    status: unsupported
    note: 建议升级至 19c
  - name: Oracle 19c
    status: supported
browser:
  label: 浏览器
  items:
    - name: Chrome 100+
      status: full
combinations:
  - os: Windows Server 2019
    db: Oracle 19c
    status: full
    note: 已通过压测
  - os: Windows Server 2016
    db: Oracle 11g
    status: unsupported
    note: 补丁依赖冲突
"""


@pytest.fixture
def matrix(write_yaml) -> object:
    return load_matrix(write_yaml("matrix.yaml", _MATRIX))


def test_load_dimensions_and_labels(matrix) -> None:
    assert matrix.product == "测试平台 v1.0"
    assert matrix.updated == "2025-11-01"
    assert [dimension.key for dimension in matrix.dimensions] == ["os", "db", "browser"]
    assert matrix.dimension("browser").label == "浏览器"
    assert matrix.dimension("db").items[0].level == "unsupported"
    assert matrix.dimension("db").items[1].level == "full"  # supported 别名


def test_flat_dimension_is_supported(matrix) -> None:
    assert matrix.dimension("os").find("Windows Server 2019")[0].level == "full"


def test_unknown_top_level_field(write_yaml) -> None:
    with pytest.raises(SpecError):
        load_matrix(write_yaml("matrix.yaml", "product: p\nos: 不是列表\n"))


def test_duplicate_items(write_yaml) -> None:
    text = """
product: p
os:
  - {name: Windows, status: full}
  - {name: Windows, status: partial}
"""
    with pytest.raises(SpecError) as info:
        load_matrix(write_yaml("matrix.yaml", text))
    assert "重复" in str(info.value)


def test_unknown_status(write_yaml) -> None:
    text = """
product: p
os:
  - {name: Windows, status: 也许吧}
"""
    with pytest.raises(SpecError):
        load_matrix(write_yaml("matrix.yaml", text))


def test_missing_product(write_yaml) -> None:
    with pytest.raises(SpecError):
        load_matrix(write_yaml("matrix.yaml", "os:\n  - {name: Windows, status: full}\n"))


def test_find_exact_contains_fuzzy() -> None:
    dimension = Dimension(
        key="os",
        label="操作系统",
        items=(
            CompatItem("Windows Server 2019", "full"),
            CompatItem("Windows Server 2019 数据中心版", "partial"),
            CompatItem("Ubuntu 22.04", "full"),
        ),
    )
    assert dimension.find("Windows Server 2019")[0].name == "Windows Server 2019"
    assert dimension.find("windows server 2019")[1] == "exact"
    assert dimension.find("Ubuntu")[0].name == "Ubuntu 22.04"
    assert dimension.find("ubuntu 22")[1] in {"prefix", "contains", "fuzzy"}
    assert dimension.find("完全不存在的东西")[0] is None
    assert dimension.find("")[1] == "empty"


def test_verdict_full(matrix) -> None:
    verdict = evaluate(matrix, {"os": "Windows Server 2019", "db": "Oracle 19c"})
    assert verdict.level == "full"
    assert verdict.exit_code == 0
    assert "可以承诺" in verdict.headline
    assert verdict.combination_note == "已通过压测"


def test_verdict_unsupported_by_combination(matrix) -> None:
    verdict = evaluate(matrix, {"os": "Windows Server 2016", "db": "Oracle 11g"})
    assert verdict.level == "unsupported"
    assert verdict.exit_code == 1
    assert verdict.combination_note == "补丁依赖冲突"
    assert "不应承诺" in verdict.headline


def test_verdict_partial_from_single_dimension(matrix) -> None:
    verdict = evaluate(matrix, {"os": "Windows Server 2016", "db": "Oracle 19c"})
    assert verdict.level == "partial"
    assert verdict.exit_code == 3
    assert "有条件支持" in verdict.headline
    assert any("KB4532945" in line for line in describe(verdict))


def test_verdict_unknown_value(matrix) -> None:
    verdict = evaluate(matrix, {"os": "FreeBSD 14"})
    assert verdict.level == "unknown"
    assert verdict.exit_code == 4
    assert any("未收录" in line for line in describe(verdict))


def test_verdict_unknown_dimension(matrix) -> None:
    verdict = evaluate(matrix, {"cpu": "鲲鹏 920"})
    assert verdict.level == "unknown"
    assert any("没有这个维度" in line for line in describe(verdict))


def test_fuzzy_match_is_marked(matrix) -> None:
    verdict = evaluate(matrix, {"os": "Windows Server 2016"})
    assert verdict.dimensions[0].item is not None
    assert verdict.dimensions[0].matched_by == "exact"


def test_markdown_table(matrix) -> None:
    markdown = render_markdown(matrix, brand="测试公司")
    assert "## 测试平台 v1.0 · 兼容性矩阵" in markdown
    assert "| 环境 | 支持情况 | 说明 |" in markdown
    assert "| Windows Server 2016 | 部分支持 | 需补丁 KB4532945 |" in markdown
    assert "测试公司" in markdown


def test_html_is_offline(matrix) -> None:
    html = render_html(matrix, brand="测试公司")
    assert "<!doctype html>" in html
    assert "测试平台 v1.0" in html
    assert "补丁依赖冲突" in html
    assert "<script" not in html
    assert not re.search(r"https?://", html)


def test_json_payload(matrix) -> None:
    import json

    verdict = evaluate(matrix, {"os": "CentOS 7.9"})
    payload = json.loads(verdict_json(verdict, matrix))
    assert payload["level"] == "partial"
    assert payload["dimensions"][0]["matched"] == "CentOS 7.9"
