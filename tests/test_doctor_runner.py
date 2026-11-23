"""执行器：跑检查、按标签筛选、修复后复检。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.registry import check as register
from pre_sales_kit.doctor.runner import DoctorRunner
from pre_sales_kit.doctor.spec import load_spec


@register("marker_test", "测试用：标记文件存在才算通过")
def run_marker(spec: CheckSpec, ctx: CheckContext) -> list[Item]:
    path = ctx.base_dir / str(spec.params.get("file", "marker.txt"))
    if path.exists():
        return [Item("标记文件", Status.PASS, f"{path.name} 存在")]
    return [Item("标记文件", Status.FAIL, f"{path.name} 不存在", hint="配 fix 可以自动创建")]


_SPEC = """
version: 1
title: 测试体检
checks:
  - id: marker
    name: 标记文件
    type: marker_test
    tags: [快速]
    params:
      file: marker.txt
    fix:
      action: run_command
      command: "{python} {spec_dir}/make_marker.py"
      risk: {risk}
  - id: slow
    name: 慢检查
    type: marker_test
    tags: [慢]
    params:
      file: other.txt
"""


def _prepare(tmp_path: Path, risk: str = "low") -> Path:
    (tmp_path / "make_marker.py").write_text(
        "from pathlib import Path\nPath(__file__).with_name('marker.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "doctor.yaml"
    spec_path.write_text(_SPEC.replace("{risk}", risk), encoding="utf-8")
    return spec_path


def test_run_reports_failure(tmp_path) -> None:
    runner = DoctorRunner(load_spec(_prepare(tmp_path)))
    report = runner.run()
    assert report.results[0].status is Status.FAIL
    assert report.exit_code() == 1
    assert report.failed
    assert "标记文件" in report.verdict_text()


def test_fix_creates_marker_and_rechecks(tmp_path) -> None:
    runner = DoctorRunner(load_spec(_prepare(tmp_path)))
    report = runner.run()
    assert report.results[0].status is Status.FAIL

    outcomes = runner.fix(report, yes=False)
    assert len(outcomes) == 1
    assert outcomes[0].ok is True
    assert outcomes[0].status_after is Status.PASS
    assert "复检通过" in outcomes[0].detail
    # 修复后报告里的结果被替换成复检结果
    assert report.results[0].status is Status.PASS
    # 另一条没有配 fix 的检查仍然是失败，退出码据此为 1
    assert report.exit_code() == 1
    assert [result.spec.id for result in report.failed] == ["slow"]


def test_high_risk_fix_needs_yes(tmp_path) -> None:
    runner = DoctorRunner(load_spec(_prepare(tmp_path, risk="high")))
    report = runner.run()
    outcomes = runner.fix(report, yes=False)
    assert outcomes[0].skipped is True
    assert outcomes[0].status_before is Status.FAIL
    assert not (tmp_path / "marker.txt").exists()


def test_only_and_skip_filters(tmp_path) -> None:
    spec = load_spec(_prepare(tmp_path))
    only_report = DoctorRunner(spec).run(only=["快速"])
    assert [result.spec.id for result in only_report.results] == ["marker"]

    skipped_report = DoctorRunner(spec).run(skip=["慢"])
    assert [result.spec.id for result in skipped_report.results] == ["marker"]


def test_filter_that_matches_nothing(tmp_path) -> None:
    from pre_sales_kit.core.errors import KitError

    spec = load_spec(_prepare(tmp_path))
    with pytest.raises(KitError):
        DoctorRunner(spec).run(only=["不存在"])


def test_progress_callback_streams(tmp_path) -> None:
    seen: list[str] = []
    runner = DoctorRunner(load_spec(_prepare(tmp_path)))
    runner.run(progress=lambda result: seen.append(result.spec.id))
    assert seen == ["marker", "slow"]


def test_report_to_dict_is_json_ready(tmp_path) -> None:
    import json

    runner = DoctorRunner(load_spec(_prepare(tmp_path)))
    report = runner.run()
    payload = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    assert payload["checks"][0]["id"] == "marker"
    assert payload["counts"]["fail"] == 2
    assert payload["host"]["平台"]


def test_timeout_is_reported_as_failure() -> None:
    """检查卡住时不能把整场体检挂死。"""
    from pre_sales_kit.doctor.registry import get

    assert get("marker_test")

    @register("hang_test", "测试用：故意睡很久")
    def run_hang(spec: CheckSpec, ctx: CheckContext) -> list[Item]:  # noqa: ARG001
        import time

        time.sleep(3)
        return [Item("挂起", Status.PASS, "不该等到这里")]

    spec = CheckSpec(
        id="hang",
        name="会挂起的检查",
        type="hang_test",
        params={},
        timeout=0.2,
        source="test.yaml",
    )
    from pre_sales_kit.doctor.models import DoctorSpec

    runner = DoctorRunner(DoctorSpec(title="t", source="test.yaml", checks=(spec,)))
    started = datetime.now()
    report = runner.run()
    took = (datetime.now() - started).total_seconds()
    assert report.results[0].status is Status.FAIL
    assert "超时" in report.results[0].items[0].detail
    assert took < 2.5
