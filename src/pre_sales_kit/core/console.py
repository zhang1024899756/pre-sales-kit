"""终端输出层。

一条原则：终端里看到的每一个符号、每一句话，HTML 报告里都必须是同一个词。
所以状态、颜色、措辞全部从这里出。
"""

from __future__ import annotations

import os
import sys
import unicodedata

from rich.console import Console
from rich.text import Text

from pre_sales_kit.core.status import Status

#: 只有品牌词用它；状态色之外不再引入第四种颜色
ACCENT = "bright_cyan"

STYLE = {
    Status.PASS: "green",
    Status.WARN: "yellow",
    Status.FAIL: "red",
    Status.SKIP: "bright_black",
}

DIM = "bright_black"
WIDTH = 64


def cjk_len(text: str) -> int:
    """按中文终端的显示宽度算长度：中日韩字符与「宽度歧义字符」都算 2 格。

    Rich 自带的 cell_len 把 ℃ / – / × 这类字符算 1 格，而中文终端按 2 格渲染，
    于是表格会整体错位。这里统一按终端的真实表现来。
    """
    total = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
    return total


def pad_cell(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - cjk_len(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def print_table(
    console: Console,
    headers: list[str],
    rows: list[list[str | Text]],
    *,
    aligns: list[str] | None = None,
    indent: int = 2,
    gap: int = 2,
) -> None:
    """自己画的对齐表格：中文终端下每一列都严丝合缝。"""
    aligns = aligns or ["left"] * len(headers)
    plain_rows: list[list[str]] = [[_plain(cell) for cell in row] for row in rows]
    widths = [cjk_len(header) for header in headers]
    for plain in plain_rows:
        for index, cell in enumerate(plain):
            widths[index] = max(widths[index], cjk_len(cell))

    def emit(cells: list[str | Text]) -> None:
        line = Text(" " * indent)
        for index, cell in enumerate(cells):
            if index:
                line.append(" " * gap)
            if isinstance(cell, Text):
                # 带样式的单元格也要补齐，否则表头和数据的列会错开
                padded = cell.copy()
                missing = max(0, widths[index] - cjk_len(cell.plain))
                if aligns[index] == "right":
                    padded.pad_left(missing)
                else:
                    padded.pad_right(missing)
                line.append_text(padded)
            else:
                line.append(pad_cell(cell, widths[index], aligns[index]))
        console.print(line)

    emit([Text(header, style=DIM) for header in headers])
    for row in rows:
        emit(row)


def _plain(cell: str | Text) -> str:
    return cell.plain if isinstance(cell, Text) else str(cell)


def make_console(*, no_color: bool = False) -> Console:
    """统一构造 Console：尊重 NO_COLOR 与 --no-color，非 TTY 时降级为纯文本。"""
    force_terminal = None
    if no_color or os.environ.get("NO_COLOR"):
        force_terminal = False
    return Console(
        theme=None,
        force_terminal=force_terminal,
        no_color=no_color or bool(os.environ.get("NO_COLOR")),
        highlight=False,
        soft_wrap=False,
        width=max(60, min(100, _terminal_width())),
    )


def _terminal_width() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError):
        return 92


def banner(console: Console, title: str, lines: list[str]) -> None:
    """顶部：品牌行 + 上下文（名称、时间、平台）。"""
    console.print()
    head = Text()
    head.append("  kit", style=f"bold {ACCENT}")
    head.append(f"  ·  {title}", style="bold")
    console.print(head)
    for line in lines:
        if line:
            console.print(Text(f"  {line}", style=DIM))
    console.print()


def section(console: Console, title: str) -> None:
    console.print(Text(f"  {title}", style="bold"))


def check_line(
    console: Console,
    status: Status,
    label: str,
    detail: str = "",
    *,
    label_width: int = 26,
    indent: int = 4,
) -> None:
    """一行检查结果：✓ 名称               细节

    细节超宽时按显示宽度折行，续行对齐到细节列——终端里的中文占两格，
    直接用 len() 算会歪，所以按 cell 宽度来。
    """
    label_cell = cjk_len(label)
    padding = max(2, label_width - label_cell)
    detail_column = indent + 2 + label_cell + padding
    available = max(24, console.width - detail_column)

    line = Text(" " * indent)
    line.append(f"{status.glyph} ", style=STYLE[status])
    line.append(label)
    line.append(" " * padding)
    detail_style = DIM if status is Status.PASS else STYLE[status]
    if not detail:
        console.print(line)
        return

    chunks = wrap_cells(detail, available)
    line.append(chunks[0], style=detail_style)
    console.print(line)
    for chunk in chunks[1:]:
        console.print(Text(" " * detail_column + chunk, style=detail_style))


def wrap_cells(text: str, width: int) -> list[str]:
    """按终端显示宽度折行，优先在空格处断开。"""
    if width <= 0 or cjk_len(text) <= width:
        return [text]
    chunks: list[str] = []
    rest = text
    while cjk_len(rest) > width:
        consumed = 0
        cut = 0
        for index, char in enumerate(rest):
            step = cjk_len(char)
            if consumed + step > width:
                break
            consumed += step
            cut = index + 1
        # 回退到最近的空格，避免把单词/路径从中间劈开
        space = rest.rfind(" ", 0, cut + 1)
        if space >= max(1, width // 3):
            cut = space + 1
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
        if not rest:
            break
    if rest:
        chunks.append(rest)
    return chunks or [text]


def sub_line(console: Console, text: str, *, indent: int = 8) -> None:
    """缩进说明 / 提示，跟在检查行下面。"""
    console.print(Text(" " * indent + text, style=DIM))


def hint_line(console: Console, text: str, *, indent: int = 6) -> None:
    """提示语：超宽时折行，续行保持同样的缩进。"""
    chunks = wrap_cells(text, max(24, console.width - indent))
    for chunk in chunks:
        console.print(Text(" " * indent + chunk, style=DIM))


def summary(console: Console, counts: dict[Status, int], duration: float) -> None:
    """底部计数行，像仪表读数。"""
    from pre_sales_kit.core.text import format_duration

    console.print()
    console.print(Text("  " + "─" * (WIDTH - 2), style=DIM))
    line = Text("  ")
    first = True
    for status in (Status.PASS, Status.WARN, Status.FAIL, Status.SKIP):
        if counts.get(status, 0) == 0:
            continue
        if not first:
            line.append(" · ", style=DIM)
        line.append(str(counts[status]), style=f"bold {STYLE[status]}")
        line.append(f" {status.label}", style=DIM)
        first = False
    if first:
        line.append("没有任何检查项", style=DIM)
    total = sum(counts.values())
    line.append(f"          共 {total} 项 · {format_duration(duration)}", style=DIM)
    console.print(line)


def verdict(console: Console, status: Status, message: str) -> None:
    line = Text("  ")
    line.append(f"{status.glyph} ", style=f"bold {STYLE[status]}")
    line.append(message)
    console.print(line)


def detail_path(console: Console, label: str, path: str) -> None:
    line = Text("  ")
    line.append(f"{label} → ", style=DIM)
    line.append(path, style=ACCENT)
    console.print(line)


def error(console: Console, message: str, hint: str | None = None) -> None:
    line = Text("  ✗ ", style="bold red")
    line.append(message)
    console.print(line)
    if hint:
        console.print(Text(f"    {hint}", style=DIM))


def muted(console: Console, text: str, *, indent: int = 2) -> None:
    console.print(Text(" " * indent + text, style=DIM))
