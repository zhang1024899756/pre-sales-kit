"""兼容性结论的三种呈现：终端、单文件 HTML、Markdown（贴进方案书）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.text import Text

from pre_sales_kit.compat.models import (
    LEVEL_LABELS,
    CompatMatrix,
    Verdict,
)
from pre_sales_kit.core.console import check_line, print_table, section
from pre_sales_kit.core.status import Status
from pre_sales_kit.core.text import display_source

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_LEVEL_STYLE = {
    "full": "green",
    "partial": "yellow",
    "unsupported": "red",
    "unknown": "bright_black",
}


def level_text(level: str) -> Text:
    return Text(LEVEL_LABELS.get(level, level), style=_LEVEL_STYLE.get(level, "white"))


# ---------------- 终端 ----------------


def print_verdict(console: Console, verdict: Verdict, matrix: CompatMatrix) -> None:
    from pre_sales_kit.core.console import banner

    banner(console, "兼容性查询", [matrix.product, display_source(matrix.source)])
    rows: list[list[str | Text]] = []
    for item in verdict.dimensions:
        rows.append(
            [
                Text(item.label, style="bright_black"),
                Text(item.query),
                level_text(item.item.level) if item.item else Text("未收录"),
                Text(item.item.note or "—", style="bright_black") if item.item else Text(item.reason or "", style="bright_black"),
            ]
        )
    print_table(
        console,
        ["维度", "查询值", "结论", "说明"],
        rows,
        aligns=["left", "left", "left", "left"],
        gap=3,
    )
    console.print()
    section(console, "结论")
    status = {
        "full": Status.PASS,
        "partial": Status.WARN,
        "unsupported": Status.FAIL,
        "unknown": Status.SKIP,
    }.get(verdict.level, Status.SKIP)
    from pre_sales_kit.core.console import hint_line

    check_line(console, status, verdict.label, verdict.headline, label_width=8)
    hint_line(console, f"→ {verdict.suggestion}", indent=4)
    console.print()


def print_matrix(console: Console, matrix: CompatMatrix) -> None:
    from pre_sales_kit.core.console import banner, muted

    subtitle = matrix.product + (f" · 更新于 {matrix.updated}" if matrix.updated else "")
    banner(console, "兼容性矩阵", [subtitle, display_source(matrix.source)])

    for dimension in matrix.dimensions:
        section(console, dimension.label)
        rows: list[list[str | Text]] = []
        for item in dimension.items:
            rows.append(
                [
                    Text(item.name),
                    level_text(item.level),
                    Text(item.note or "—", style="bright_black"),
                ]
            )
        print_table(console, ["环境", "支持情况", "说明"], rows, aligns=["left", "left", "left"], gap=3)
        console.print()

    if matrix.combinations:
        section(console, "组合结论")
        rows = []
        for combination in matrix.combinations:
            rows.append(
                [
                    Text(" + ".join(combination.values.values())),
                    level_text(combination.level),
                    Text(combination.note or "—", style="bright_black"),
                ]
            )
        print_table(console, ["组合", "结论", "说明"], rows, aligns=["left", "left", "left"], gap=3)
        console.print()

    muted(console, "加 --html out/matrix.html 生成可贴进方案书的表格", indent=2)
    muted(console, "加 --md out/matrix.md 生成 Markdown 表格", indent=2)
    console.print()


# ---------------- HTML / Markdown ----------------


def build_view(matrix: CompatMatrix, *, brand: str | None = None) -> dict[str, Any]:
    counts = {level: 0 for level in LEVEL_LABELS}
    for dimension in matrix.dimensions:
        for item in dimension.items:
            counts[item.level] = counts.get(item.level, 0) + 1
    return {
        "product": matrix.product,
        "brand": brand,
        "updated": matrix.updated,
        "source": matrix.source,
        "notes": matrix.notes,
        "generated": datetime.now().astimezone(),
        "counts": [
            {"level": level, "label": label, "count": counts.get(level, 0)}
            for level, label in LEVEL_LABELS.items()
        ],
        "total": sum(counts.values()),
        "dimensions": [
            {
                "key": dimension.key,
                "label": dimension.label,
                # 键名不能叫 items：Jinja 里 dimension.items 是 dict 的方法
                "entries": [
                    {
                        "name": item.name,
                        "level": item.level,
                        "label": item.label,
                        "note": item.note,
                        "since": item.since,
                    }
                    for item in dimension.items
                ],
            }
            for dimension in matrix.dimensions
        ],
        "combinations": [
            {
                "values": list(combination.values.values()),
                "pairs": [
                    {"label": _label_for(matrix, key), "value": value}
                    for key, value in combination.values.items()
                ],
                "level": combination.level,
                "label": LEVEL_LABELS.get(combination.level, combination.level),
                "note": combination.note,
            }
            for combination in matrix.combinations
        ],
    }


def _label_for(matrix: CompatMatrix, key: str) -> str:
    dimension = matrix.dimension(key)
    return dimension.label if dimension else key


def render_html(matrix: CompatMatrix, *, brand: str | None = None) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template("compat_matrix.html.j2").render(
        view=build_view(matrix, brand=brand)
    )


def write_html(matrix: CompatMatrix, path: Path | str, *, brand: str | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(matrix, brand=brand), encoding="utf-8")
    return target


def render_markdown(matrix: CompatMatrix, *, brand: str | None = None) -> str:
    lines: list[str] = []
    title = matrix.product
    lines.append(f"## {title} · 兼容性矩阵")
    meta = []
    if brand:
        meta.append(brand)
    if matrix.updated:
        meta.append(f"更新日期：{matrix.updated}")
    if meta:
        lines.append("")
        lines.append(" ｜ ".join(meta))
    lines.append("")

    for dimension in matrix.dimensions:
        lines.append(f"### {dimension.label}")
        lines.append("")
        lines.append("| 环境 | 支持情况 | 说明 |")
        lines.append("| --- | --- | --- |")
        for item in dimension.items:
            note = (item.note or "").replace("|", "\\|")
            lines.append(f"| {item.name} | {item.label} | {note or '—'} |")
        lines.append("")

    if matrix.combinations:
        lines.append("### 已验证的组合")
        lines.append("")
        headers = " | ".join(_label_for(matrix, key) for key in matrix.combinations[0].values)
        lines.append(f"| {headers} | 结论 | 说明 |")
        lines.append("| " + " | ".join(["---"] * (len(matrix.combinations[0].values) + 2)) + " |")
        for combination in matrix.combinations:
            values = " | ".join(combination.values.values())
            note = (combination.note or "—").replace("|", "\\|")
            lines.append(
                f"| {values} | {LEVEL_LABELS.get(combination.level, combination.level)} | {note} |"
            )
        lines.append("")

    if matrix.notes:
        lines.append(f"> {matrix.notes}")
        lines.append("")
    return "\n".join(lines)


def write_markdown(matrix: CompatMatrix, path: Path | str, *, brand: str | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(matrix, brand=brand), encoding="utf-8")
    return target


def verdict_json(verdict: Verdict, matrix: CompatMatrix) -> str:
    payload = {
        "product": matrix.product,
        "level": verdict.level,
        "label": verdict.label,
        "headline": verdict.headline,
        "suggestion": verdict.suggestion,
        "dimensions": [
            {
                "key": item.key,
                "label": item.label,
                "query": item.query,
                "matched": item.item.name if item.item else None,
                "level": item.item.level if item.item else "unknown",
                "note": item.item.note if item.item else item.reason,
            }
            for item in verdict.dimensions
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

