"""数据检查：演示数据还在不在、够不够、是不是停在「今天」。

「演示数据一眼假」里最惨的一种不是数值假，而是时间停在三天前。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from pre_sales_kit.core.status import Status
from pre_sales_kit.core.text import format_ago, parse_timestamp
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = ("path", "table", "time_column", "min_rows", "max_rows", "latest_within", "where")


@check("data", "演示数据：记录数、时间新鲜度")
def run_data(spec: CheckSpec, ctx: CheckContext) -> list[Item]:
    params = Params.of(spec, _ALLOWED)
    raw_path = params.text("path", required=True)
    table = params.text("table", required=True)
    assert raw_path and table
    time_column = params.text("time_column")
    min_rows = params.count("min_rows")
    max_rows = params.count("max_rows")
    latest_within = params.duration("latest_within")
    where = params.text("where")

    path = Path(raw_path)
    if not path.is_absolute():
        path = (ctx.base_dir / path).resolve()
    label = table

    if not path.exists():
        return [
            Item(
                label,
                Status.FAIL,
                f"数据文件不存在：{_display_path(path, ctx.base_dir)}",
                hint="先跑 kit seed 生成演示数据；可配 fix: reseed",
            )
        ]

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return [Item(label, Status.FAIL, f"打不开数据库：{exc}")]
    try:
        tables = _tables(connection)
        if table not in tables:
            suggestion = _similar(table, tables)
            hint = f"库里有这些表：{', '.join(tables) or '（空库）'}"
            if suggestion:
                hint += f"；是不是想写 {suggestion}？"
            return [Item(label, Status.FAIL, f"表 {table} 不存在", hint=hint)]

        clause = f" WHERE {where}" if where else ""
        try:
            rows = connection.execute(f"SELECT COUNT(*) FROM {table}{clause}").fetchone()[0]
        except sqlite3.Error as exc:
            return [Item(label, Status.FAIL, f"查询行数失败：{exc}")]

        problems: list[str] = []
        passed: list[str] = []
        if min_rows is not None and rows < min_rows:
            problems.append(f"只有 {rows:,} 行（要求 ≥ {min_rows:,}）")
        if max_rows is not None and rows > max_rows:
            problems.append(f"有 {rows:,} 行（要求 ≤ {max_rows:,}）")
        passed.append(f"{rows:,} 行")

        latest_text = ""
        if latest_within is not None:
            if not time_column:
                return [
                    Item(
                        label,
                        Status.FAIL,
                        "配了 latest_within 但没写 time_column",
                        hint="补上时间列名，例如 time_column: ts",
                    )
                ]
            try:
                raw_latest = connection.execute(f"SELECT MAX({time_column}) FROM {table}").fetchone()[0]
            except sqlite3.Error as exc:
                return [Item(label, Status.FAIL, f"读时间列 {time_column} 失败：{exc}")]
            latest = parse_timestamp(raw_latest)
            if latest is None:
                problems.append(f"时间列 {time_column} 里没有可识别的时间")
            else:
                age = (datetime.now().astimezone() - latest).total_seconds()
                latest_text = f"最新 {format_ago(age)}"
                if age > latest_within:
                    problems.append(
                        f"最新数据 {format_ago(age)}（要求 ≤ {_human_threshold(latest_within)}）"
                    )

        if problems:
            return [
                Item(
                    label,
                    Status.FAIL,
                    "；".join(problems),
                    hint="演示数据陈旧是客户一眼看穿假数据的第一破绽；可配 fix: reseed",
                )
            ]
        detail = " · ".join(item for item in (*passed, latest_text) if item)
        return [Item(label, Status.PASS, f"{table} · {detail}")]
    finally:
        connection.close()


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        return str(path.relative_to(base_dir.resolve()))
    except ValueError:
        return str(path)


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [row[0] for row in rows]


def _similar(name: str, candidates: list[str]) -> str | None:
    import difflib

    match = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
    return match[0] if match else None


def _human_threshold(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时"
    return f"{seconds / 86400:.1f} 天"
