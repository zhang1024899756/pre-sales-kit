"""doctor 的数据模型：声明（spec）与结果（result）。

设计上刻意把「检查怎么写」和「结果怎么展示」分开：检查函数只产出 Item，
终端、HTML、JSON 三种输出都从同一份 DoctorReport 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pre_sales_kit.core.status import Status, worst

#: fix.action 支持的取值
FIX_ACTIONS = ("kill_port", "start_service", "run_command", "reseed")

#: 修复风险等级：high 需要显式 --yes
FIX_RISKS = ("low", "high")


@dataclass(frozen=True)
class FixSpec:
    """一条检查的修复方式。写在 YAML 里，不写死在代码里。"""

    action: str
    command: str | None = None
    wait: float = 2.0
    risk: str = "low"
    note: str | None = None

    @property
    def label(self) -> str:
        return {
            "kill_port": "释放端口",
            "start_service": "拉起服务",
            "run_command": "执行修复命令",
            "reseed": "重置演示数据",
        }.get(self.action, self.action)


@dataclass(frozen=True)
class CheckSpec:
    """一条检查的声明。"""

    id: str
    name: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    fix: FixSpec | None = None
    tags: tuple[str, ...] = ()
    timeout: float | None = None
    description: str | None = None
    source: str = ""


@dataclass(frozen=True)
class DoctorSpec:
    """一份可执行的体检声明。"""

    title: str
    source: str
    checks: tuple[CheckSpec, ...]
    name: str | None = None
    description: str | None = None


@dataclass
class Item:
    """检查的单个观测点。一条检查可以产出多个（例如环境检查同时看磁盘和时区）。"""

    label: str
    status: Status
    detail: str = ""
    hint: str | None = None


@dataclass
class CheckResult:
    """一条检查的完整结果。"""

    spec: CheckSpec
    items: list[Item] = field(default_factory=list)
    duration: float = 0.0
    error: str | None = None

    @property
    def status(self) -> Status:
        if self.error:
            return Status.FAIL
        return worst([item.status for item in self.items])

    @property
    def fixable(self) -> bool:
        return self.spec.fix is not None

    def problems(self) -> list[Item]:
        return [item for item in self.items if item.status in (Status.WARN, Status.FAIL)]


@dataclass
class FixOutcome:
    """一次修复尝试的结果（包含修复后的复检）。"""

    check_id: str
    check_name: str
    action: str
    action_label: str
    status_before: Status
    status_after: Status | None
    ok: bool
    detail: str
    duration: float = 0.0
    skipped: bool = False
    skip_reason: str | None = None
    rechecked: CheckResult | None = None

    @property
    def display_status(self) -> Status:
        if self.skipped:
            return Status.SKIP
        if self.ok:
            return Status.PASS
        return Status.FAIL


@dataclass
class DoctorReport:
    """一次体检的全部事实。"""

    title: str
    name: str | None
    source: str
    started_at: datetime
    finished_at: datetime
    results: list[CheckResult] = field(default_factory=list)
    fixes: list[FixOutcome] = field(default_factory=list)
    host: dict[str, str] = field(default_factory=dict)
    command: str = ""
    version: str = ""

    @property
    def duration(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def counts(self) -> dict[Status, int]:
        tally = {status: 0 for status in Status}
        for result in self.results:
            tally[result.status] += 1
        return tally

    @property
    def overall(self) -> Status:
        statuses = [result.status for result in self.results]
        return worst(statuses) if statuses else Status.SKIP

    @property
    def failed(self) -> list[CheckResult]:
        return [result for result in self.results if result.status is Status.FAIL]

    @property
    def warned(self) -> list[CheckResult]:
        return [result for result in self.results if result.status is Status.WARN]

    def exit_code(self, *, strict: bool = False) -> int:
        if self.failed:
            return 1
        if strict and self.warned:
            return 1
        return 0

    def verdict_text(self) -> str:
        if self.failed:
            first = self.failed[0]
            return f"有 {len(self.failed)} 项失败：先处理「{first.spec.name}」再出发"
        if self.warned:
            first = self.warned[0]
            return f"可以出发，但有 {len(self.warned)} 项警告（「{first.spec.name}」等），现场留意"
        return "全部通过，可以出发"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "name": self.name,
            "source": self.source,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration": round(self.duration, 3),
            "overall": self.overall.value,
            "command": self.command,
            "version": self.version,
            "host": self.host,
            "counts": {status.value: count for status, count in self.counts.items()},
            "checks": [
                {
                    "id": result.spec.id,
                    "name": result.spec.name,
                    "type": result.spec.type,
                    "status": result.status.value,
                    "duration": round(result.duration, 4),
                    "error": result.error,
                    "items": [
                        {
                            "label": item.label,
                            "status": item.status.value,
                            "detail": item.detail,
                            "hint": item.hint,
                        }
                        for item in result.items
                    ],
                }
                for result in self.results
            ],
            "fixes": [
                {
                    "check_id": fix.check_id,
                    "action": fix.action,
                    "status_before": fix.status_before.value,
                    "status_after": fix.status_after.value if fix.status_after else None,
                    "ok": fix.ok,
                    "skipped": fix.skipped,
                    "detail": fix.detail,
                }
                for fix in self.fixes
            ],
        }


@dataclass(frozen=True)
class CheckContext:
    """检查函数执行时能看到的东西。"""

    spec_path: Path
    base_dir: Path
    now: datetime
    verbose: bool = False
