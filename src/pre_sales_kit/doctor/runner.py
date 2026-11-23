"""执行体检：跑检查、跑修复、修复后复检。

修复的关键不是「执行成功」，而是「复检通过」——执行了但没修好，必须原样告诉人。
"""

from __future__ import annotations

import locale
import re
import socket
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

from pre_sales_kit import __version__
from pre_sales_kit.core.errors import FixError, KitError
from pre_sales_kit.core.proc import (
    IS_WINDOWS,
    find_listener,
    kill_process,
    platform_name,
    platform_summary,
    python_version,
    run_capture,
    start_detached,
)
from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import (
    CheckContext,
    CheckResult,
    CheckSpec,
    DoctorReport,
    DoctorSpec,
    FixOutcome,
    FixSpec,
    Item,
)
from pre_sales_kit.doctor.registry import get as get_check_type

ProgressCallback = Callable[[CheckResult], None]

#: 单条检查的兜底超时（声明里没写 timeout 时）
DEFAULT_TIMEOUT = 60.0


class DoctorRunner:
    def __init__(self, spec: DoctorSpec, *, verbose: bool = False) -> None:
        self.spec = spec
        self.verbose = verbose
        self.base_dir = Path(spec.source).parent
        self.context = CheckContext(
            spec_path=Path(spec.source),
            base_dir=self.base_dir,
            now=datetime.now().astimezone(),
            verbose=verbose,
        )

    # ---------------- 主流程 ----------------

    def run(
        self,
        *,
        only: Iterable[str] = (),
        skip: Iterable[str] = (),
        progress: ProgressCallback | None = None,
    ) -> DoctorReport:
        started_at = datetime.now().astimezone()
        checks = self._select(only=only, skip=skip)
        results: list[CheckResult] = []
        for check_spec in checks:
            result = self._execute(check_spec)
            results.append(result)
            if progress:
                progress(result)
        return DoctorReport(
            title=self.spec.title,
            name=self.spec.name,
            source=self.spec.source,
            started_at=started_at,
            finished_at=datetime.now().astimezone(),
            results=results,
            host=self.host_info(),
            command=_command_line(),
            version=__version__,
        )

    def fix(self, report: DoctorReport, *, yes: bool = False) -> list[FixOutcome]:
        """对失败的检查尝试修复；修完立刻复检并替换结果。"""
        outcomes: list[FixOutcome] = []
        for index, result in enumerate(report.results):
            fix_spec = result.spec.fix
            if fix_spec is None:
                continue
            if result.status not in (Status.WARN, Status.FAIL):
                continue
            if fix_spec.risk == "high" and not yes:
                outcomes.append(
                    FixOutcome(
                        check_id=result.spec.id,
                        check_name=result.spec.name,
                        action=fix_spec.action,
                        action_label=fix_spec.label,
                        status_before=result.status,
                        status_after=None,
                        ok=False,
                        detail="高风险操作，需要加 --yes 才会执行",
                        skipped=True,
                        skip_reason="未加 --yes",
                    )
                )
                continue
            outcome = self._apply_fix(result)
            outcomes.append(outcome)
            if outcome.rechecked is not None:
                report.results[index] = outcome.rechecked
        report.fixes = outcomes
        self.revalidate(report)
        return outcomes

    def revalidate(self, report: DoctorReport) -> None:
        """整体复检：把仍然不通过的检查再跑一遍。

        修复会改变环境——你修好「门户端口」，没配 fix 的「趋势接口」可能就通了。
        只守着修复当下的复检，会把这种情况误报成失败。
        """
        for index, result in enumerate(report.results):
            if result.status in (Status.PASS, Status.SKIP) or result.error:
                continue
            retried = self._execute(result.spec)
            if retried.status is Status.PASS:
                report.results[index] = retried

    # ---------------- 单条检查 ----------------

    def _execute(self, check_spec: CheckSpec) -> CheckResult:
        started = time.perf_counter()
        timeout = check_spec.timeout or DEFAULT_TIMEOUT
        try:
            func = get_check_type(check_spec.type)
        except KitError as exc:
            return CheckResult(spec=check_spec, error=str(exc))
        try:
            items = _call_with_timeout(func, check_spec, self.context, timeout)
        except FutureTimeout:
            return CheckResult(
                spec=check_spec,
                items=[Item(check_spec.name, Status.FAIL, f"检查超时（{timeout:g}s 未返回）")],
                duration=time.perf_counter() - started,
            )
        except KitError as exc:
            return CheckResult(
                spec=check_spec,
                items=[Item(check_spec.name, Status.FAIL, str(exc), hint=exc.hint)],
                duration=time.perf_counter() - started,
            )
        except Exception as exc:
            return CheckResult(
                spec=check_spec,
                items=[Item(check_spec.name, Status.FAIL, f"检查执行出错：{exc}")],
                duration=time.perf_counter() - started,
            )
        if not items:
            items = [Item(check_spec.name, Status.SKIP, "检查没有返回任何结果")]
        return CheckResult(
            spec=check_spec, items=list(items), duration=time.perf_counter() - started
        )

    def _select(self, *, only: Iterable[str], skip: Iterable[str]) -> list[CheckSpec]:
        only_tags = {tag.strip() for tag in only if tag.strip()}
        skip_tags = {tag.strip() for tag in skip if tag.strip()}
        selected: list[CheckSpec] = []
        for check_spec in self.spec.checks:
            tags = set(check_spec.tags)
            if only_tags and not (tags & only_tags) and check_spec.id not in only_tags:
                continue
            if skip_tags and (tags & skip_tags or check_spec.id in skip_tags):
                continue
            selected.append(check_spec)
        if not selected:
            raise KitError(
                "筛选后没有任何检查项",
                hint="检查 --only / --skip 用的标签是否存在于声明文件里",
            )
        return selected

    # ---------------- 修复 ----------------

    def _apply_fix(self, result: CheckResult) -> FixOutcome:
        fix_spec = result.spec.fix
        assert fix_spec is not None
        started = time.perf_counter()
        try:
            detail = self._dispatch_fix(result.spec, fix_spec)
        except FixError as exc:
            return FixOutcome(
                check_id=result.spec.id,
                check_name=result.spec.name,
                action=fix_spec.action,
                action_label=fix_spec.label,
                status_before=result.status,
                status_after=None,
                ok=False,
                detail=str(exc),
                duration=time.perf_counter() - started,
            )

        rechecked = self._execute(result.spec)
        ok = rechecked.status is Status.PASS
        if ok:
            summary = f"{detail} · 复检通过"
        elif rechecked.status is Status.WARN:
            summary = f"{detail} · 复检仍有警告"
        else:
            summary = f"{detail} · 复检仍未通过"
        return FixOutcome(
            check_id=result.spec.id,
            check_name=result.spec.name,
            action=fix_spec.action,
            action_label=fix_spec.label,
            status_before=result.status,
            status_after=rechecked.status,
            ok=ok,
            detail=summary,
            duration=time.perf_counter() - started,
            rechecked=rechecked,
        )

    def _dispatch_fix(self, spec: CheckSpec, fix_spec: FixSpec) -> str:
        if fix_spec.action == "kill_port":
            port = spec.params.get("port")
            if not isinstance(port, int):
                raise FixError("kill_port 需要检查里写了 port")
            listener = find_listener(port)
            if listener is None:
                return f"端口 {port} 现在没有被占用"
            if listener.pid is None:
                raise FixError(f"端口 {port} 被占用，但拿不到占用进程（可能需要管理员权限）")
            message = kill_process(listener.pid)
            time.sleep(0.5)
            return f"{message}，端口 {port} 已释放"

        if not fix_spec.command:
            raise FixError(f"{fix_spec.action} 动作缺少 command")
        command = self._format_command(fix_spec.command, spec)

        if fix_spec.action == "start_service":
            pid = start_detached(command, cwd=self.base_dir)
            wait = max(0.0, fix_spec.wait)
            if wait:
                time.sleep(wait)
            return f"已启动（PID {pid}），等待 {wait:g}s"

        code, output = run_capture(command, timeout=max(30.0, fix_spec.wait + 120.0))
        if code != 0:
            tail = " ".join(output.split())[-160:]
            raise FixError(f"命令退出码 {code}：{tail or '（无输出）'}")
        if fix_spec.action == "reseed":
            time.sleep(max(0.0, fix_spec.wait))
        return "命令执行完成（退出码 0）"

    def _format_command(self, command: str, spec: CheckSpec) -> str:
        """把 {spec_dir}、{python}、{port} 等占位符换成实际值；认不出的保持原样。"""
        values: dict[str, Any] = {
            "spec_dir": str(self.base_dir),
            "python": sys.executable,
            "python_exe": sys.executable,
            "kit": "kit",
        }
        for key, value in spec.params.items():
            if isinstance(value, (str, int, float)):
                values[key] = value

        def substitute(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(values[key]) if key in values else match.group(0)

        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", substitute, command)

    # ---------------- 上下文 ----------------

    def host_info(self) -> dict[str, str]:
        return {
            "平台": platform_summary(),
            "Python": python_version(),
            "终端编码": locale.getpreferredencoding(False),
            "主机名": _hostname(),
        }


def _call_with_timeout(
    func: Callable[[CheckSpec, CheckContext], list[Item]],
    spec: CheckSpec,
    context: CheckContext,
    timeout: float,
) -> list[Item]:
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"check-{spec.id}")
    try:
        future = pool.submit(func, spec, context)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover
        return "unknown"


def _command_line() -> str:
    """把命令还原成用户敲的那一行；python -m 调起时显示成 kit。"""
    if not sys.argv:
        return "kit doctor"
    entry = Path(sys.argv[0]).name
    if entry in {"__main__.py", "cli.py", "-c", ""}:
        entry = "kit"
    return " ".join([entry, *sys.argv[1:]])


def exit_code(report: DoctorReport, *, strict: bool = False) -> int:
    return report.exit_code(strict=strict)


def describe_platform() -> str:
    return platform_name() + (" (Windows)" if IS_WINDOWS else "")
