"""终端里的体检报告：分组、逐行、汇总。

和 HTML 报告共用同一套状态词，保证「终端说失败，报告里也是失败」。
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from pre_sales_kit.core.console import (
    check_line,
    detail_path,
    hint_line,
    muted,
    section,
    summary,
    verdict,
)
from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import CheckResult, DoctorReport, DoctorSpec, FixOutcome

#: 检查类型 → 终端分组名
GROUP_LABELS = {
    "port": "端口",
    "service": "服务",
    "data": "数据",
    "env": "环境",
    "version": "版本",
    "network": "网络",
    "display": "显示",
}

_PREFIX = {
    Status.PASS: "可以出发",
    Status.WARN: "可以出发，但有警告",
    Status.FAIL: "先别出发",
    Status.SKIP: "没有可判断的结果",
}


class Grouper:
    def __init__(self, console: Console) -> None:
        self.console = console
        self.current: str | None = None

    def header(self, check_type: str) -> None:
        if check_type == self.current:
            return
        self.current = check_type
        section(self.console, GROUP_LABELS.get(check_type, check_type))


def print_banner(console: Console, spec: DoctorSpec, started: str, platform_line: str) -> None:
    from pre_sales_kit.core.console import banner
    from pre_sales_kit.core.text import display_source

    lines = [
        spec.name or "",
        f"{started} · {platform_line}",
        display_source(spec.source),
    ]
    banner(console, spec.title, lines)


def print_result(console: Console, result: CheckResult, grouper: Grouper | None = None) -> None:
    grouper = grouper or Grouper(console)
    grouper.header(result.spec.type)
    items = result.items
    if len(items) == 1 or not items:
        item = items[0] if items else None
        detail = result.error or (item.detail if item else "")
        check_line(console, result.status, result.spec.name, detail)
        hint = item.hint if item else None
        if hint and result.status.is_problem:
            hint_line(console, f"→ {hint}", indent=6)
        return

    for item in items:
        check_line(console, item.status, item.label, item.detail)
        if item.hint and item.status.is_problem:
            hint_line(console, f"→ {item.hint}", indent=6)


def print_report(console: Console, report: DoctorReport) -> None:
    grouper = Grouper(console)
    for result in report.results:
        print_result(console, result, grouper)
    summary(console, report.counts, report.duration)
    verdict(console, report.overall, report.verdict_text())


def print_fixes(console: Console, fixes: list[FixOutcome]) -> None:
    if not fixes:
        return
    console.print()
    section(console, "修复")
    for fix in fixes:
        label = f"{fix.action_label} · {fix.check_name}"
        check_line(console, fix.display_status, label, fix.detail, label_width=34)
        if fix.skipped and fix.skip_reason:
            muted(console, f"→ {fix.skip_reason}", indent=6)


def print_footer(
    console: Console,
    *,
    html_path: Path | None,
    show_html_hint: bool = True,
) -> None:
    if html_path is not None:
        detail_path(console, "报告", str(html_path))
    elif show_html_hint:
        muted(console, "加 --html out/doctor-report.html 可生成可直接转发的单文件报告", indent=2)
    console.print()
