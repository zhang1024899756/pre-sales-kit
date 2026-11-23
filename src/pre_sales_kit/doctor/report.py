"""把体检结果渲染成单文件 HTML。

约束：零外部依赖（没有 CDN、没有字体文件、没有 JS），双击即开，
微信发出去对方也能看——客户现场常常没网。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pre_sales_kit.core.status import Status
from pre_sales_kit.core.text import format_duration
from pre_sales_kit.doctor.models import CheckResult, DoctorReport, Item

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_VERDICT_WORDS = {
    Status.PASS: "通过",
    Status.WARN: "有条件通过",
    Status.FAIL: "未通过",
    Status.SKIP: "无结果",
}


def render_report(report: DoctorReport, *, generated_at: datetime | None = None) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("doctor_report.html.j2")
    return template.render(view=build_view(report, generated_at=generated_at))


def write_report(
    report: DoctorReport, path: Path | str, *, generated_at: datetime | None = None
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(report, generated_at=generated_at), encoding="utf-8")
    return target


def build_view(report: DoctorReport, *, generated_at: datetime | None = None) -> dict[str, Any]:
    """模板只吃这一份视图数据，避免在模板里写业务判断。"""
    generated = generated_at or datetime.now().astimezone()
    counts = [
        {
            "status": status.value,
            "label": status.label,
            "glyph": status.glyph,
            "count": report.counts.get(status, 0),
        }
        for status in Status
    ]
    from pre_sales_kit.core.text import display_source

    return {
        "title": report.title,
        "name": report.name,
        "source": display_source(report.source),
        "version": report.version,
        "command": report.command,
        "host": report.host,
        "started": report.started_at,
        "generated": generated,
        "duration": format_duration(report.duration),
        "overall": {
            "status": report.overall.value,
            "label": report.overall.label,
            "word": _VERDICT_WORDS[report.overall],
            "text": report.verdict_text(),
        },
        "counts": counts,
        "total": sum(count["count"] for count in counts),
        "checks": [_check_view(result) for result in report.results],
        "fixes": [
            {
                "check_name": fix.check_name,
                "action_label": fix.action_label,
                "status": fix.display_status.value,
                "status_label": fix.display_status.label,
                "before": fix.status_before.label,
                "after": fix.status_after.label if fix.status_after else "—",
                "detail": fix.detail,
                "duration": format_duration(fix.duration),
            }
            for fix in report.fixes
        ],
    }


def _check_view(result: CheckResult) -> dict[str, Any]:
    return {
        "id": result.spec.id,
        "name": result.spec.name,
        "type": result.spec.type,
        "tags": list(result.spec.tags),
        "status": result.status.value,
        "status_label": result.status.label,
        "glyph": result.status.glyph,
        "duration": format_duration(result.duration),
        "error": result.error,
        "fixable": result.fixable,
        # 键名不能叫 items：Jinja 里 check.items 会取到 dict 的 .items 方法
        "rows": [_item_view(item) for item in result.items],
        "multi": len(result.items) > 1,
    }


def _item_view(item: Item) -> dict[str, Any]:
    return {
        "label": item.label,
        "status": item.status.value,
        "status_label": item.status.label,
        "glyph": item.status.glyph,
        "detail": item.detail,
        "hint": item.hint,
    }
