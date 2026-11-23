"""终端宽度与表格对齐：中文按两格算，别让整列错位。"""

from __future__ import annotations

import io

from rich.console import Console

from pre_sales_kit.core.console import cjk_len, pad_cell, print_table, wrap_cells


def test_cjk_len_counts_wide_and_ambiguous() -> None:
    assert cjk_len("abc") == 3
    assert cjk_len("中文") == 4
    assert cjk_len("℃") == 2  # 中文终端按两格渲染
    assert cjk_len("主轴温度 ℃") == 11  # 4 个汉字 + 空格 + ℃


def test_pad_cell_aligns() -> None:
    assert pad_cell("中文", 6) == "中文  "
    assert pad_cell("中文", 6, "right") == "  中文"
    assert cjk_len(pad_cell("中文", 6, "right")) == 6


def test_wrap_cells_breaks_on_space() -> None:
    chunks = wrap_cells("第一个词 第二个词 第三个词 第四个词", 12)
    assert all(cjk_len(chunk) <= 12 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == "第一个词第二个词第三个词第四个词"


def test_print_table_columns_line_up() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, no_color=True, force_terminal=False)
    print_table(
        console,
        ["点位", "模型", "范围"],
        [["主轴温度 ℃", "thermal", "42.4 ~ 47.2"], ["功率因数", "random_walk", "0.85 ~ 0.95"]],
        aligns=["left", "left", "right"],
    )
    lines = buffer.getvalue().splitlines()
    assert len(lines) == 3

    def column_offset(line: str, token: str) -> int:
        """token 在视觉上的起始列（按中文宽度算）。"""
        return cjk_len(line[: line.index(token)])

    # 左对齐列：三行从同一个视觉列开始
    assert column_offset(lines[0], "模型") == column_offset(lines[1], "thermal")
    assert column_offset(lines[0], "模型") == column_offset(lines[2], "random_walk")
    # 右对齐列：三行在同一个视觉列结束（表头也要补齐）
    assert all(cjk_len(line) == cjk_len(lines[0]) for line in lines)
