"""单文件 HTML 报告：内容对、结构对、离线可用。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import (
    CheckResult,
    CheckSpec,
    DoctorReport,
    FixOutcome,
    Item,
)
from pre_sales_kit.doctor.report import render_report, write_report


def _report(with_fix: bool = False) -> DoctorReport:
    started = datetime(2026, 11, 23, 14, 20, tzinfo=datetime.now().astimezone().tzinfo)
    port_spec = CheckSpec(id="portal", name="演示门户端口", type="port", params={"port": 8080})
    data_spec = CheckSpec(id="data", name="演示数据", type="data", params={})
    results = [
        CheckResult(
            spec=port_spec,
            items=[Item("127.0.0.1:8080", Status.PASS, "正在监听 · 12ms")],
            duration=0.012,
        ),
        CheckResult(
            spec=data_spec,
            items=[
                Item("measurements", Status.WARN, "320 行（要求 ≥ 1,000）", hint="先跑 kit seed"),
            ],
            duration=0.004,
        ),
    ]
    fixes = [
        FixOutcome(
            check_id="data",
            check_name="演示数据",
            action="reseed",
            action_label="重置演示数据",
            status_before=Status.WARN,
            status_after=Status.PASS,
            ok=True,
            detail="命令执行完成（退出码 0） · 复检通过",
        )
    ] if with_fix else []
    return DoctorReport(
        title="客户现场演示环境自检",
        name="测试客户",
        source=str(__import__("pathlib").Path("examples/doctor.yaml")),
        started_at=started,
        finished_at=started + timedelta(seconds=3.4),
        results=results,
        fixes=fixes,
        host={"平台": "Windows 11 10.0.22631", "Python": "3.11.2"},
        command="kit doctor examples/doctor.yaml",
        version="0.1.0",
    )


def test_render_contains_everything_important() -> None:
    html = render_report(_report())
    assert "<!doctype html>" in html
    assert 'charset="utf-8"' in html
    assert "客户现场演示环境自检" in html
    assert "演示门户端口" in html
    assert "正在监听 · 12ms" in html
    assert "可以出发，但有 1 项警告" in html
    assert "先跑 kit seed" in html
    assert "Windows 11 10.0.22631" in html


def test_report_is_offline_and_static() -> None:
    html = render_report(_report())
    assert "<script" not in html
    assert not re.search(r"https?://", html)
    assert "<link" not in html
    assert "@import" not in html


def test_single_item_checks_do_not_duplicate_glyph() -> None:
    html = render_report(_report())
    # 单明细检查用 check-detail，不再渲染第二个勾选行
    assert "check-detail" in html


def test_fix_section_only_when_present() -> None:
    assert "修复记录" not in render_report(_report())
    assert "修复记录" in render_report(_report(with_fix=True))


def test_write_report_creates_file(tmp_path) -> None:
    path = write_report(_report(), tmp_path / "out" / "report.html")
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")
