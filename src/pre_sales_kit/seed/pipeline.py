"""seed 的执行流程：读声明 → 生成 → 自检 → 落盘 → 打印。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.text import Text

from pre_sales_kit.core.console import banner, muted, print_table, section
from pre_sales_kit.core.errors import KitError
from pre_sales_kit.core.status import Status
from pre_sales_kit.core.text import (
    display_source,
    format_duration,
    human_size,
    parse_duration,
    parse_time_point,
)
from pre_sales_kit.seed import sinks
from pre_sales_kit.seed.faults import FAULT_KINDS, fault_label
from pre_sales_kit.seed.models import FaultSpec
from pre_sales_kit.seed.preview import write_preview
from pre_sales_kit.seed.service import (
    SeedRun,
    _jump_limit,
    build_run,
    validate_fault_points,
)
from pre_sales_kit.seed.spec import load_seed_spec

_FORMATS = ("auto", "sqlite", "csv", "jsonl", "api")

_EXTENSIONS = {
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
}


@dataclass
class SeedOptions:
    model: Path
    out: str = "demo.db"
    format: str = "auto"
    start: str = "-24h"
    end: str = "now"
    interval: str | None = None
    seed: int = 42
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    batch_size: int = 500
    timeout: float = 10.0
    dry_run: bool = False
    html: Path | None = None
    inject_fault: str | None = None
    fault_point: str | None = None
    fault_kind: str = "ramp"
    fault_magnitude: float = 0.23
    verify: bool = True
    quiet: bool = False
    console: Console | None = None


def execute(options: SeedOptions) -> int:
    console = options.console or Console()
    try:
        spec = load_seed_spec(options.model)
        start = parse_time_point(options.start)
        end = parse_time_point(options.end)
        interval = parse_duration(options.interval) if options.interval else spec.interval
        target_format = _resolve_format(options)
        extra_faults = _extra_faults(options, spec)
        run = build_run(
            spec,
            start=start,
            end=end,
            interval=interval,
            seed=options.seed,
            extra_faults=extra_faults,
        )
    except KitError as exc:
        _print_error(console, exc)
        return 2

    if not options.quiet:
        _print_header(console, run, spec)

    try:
        result = _write(run, options, target_format)
    except KitError as exc:
        _print_error(console, exc)
        return 2

    _print_result(console, run, result, options)
    if options.html is not None:
        preview_path = write_preview(run, options.html)
        _print_path(console, "预览", str(preview_path))

    if not options.verify:
        return 0
    return _print_verification(console, run)


def _resolve_format(options: SeedOptions) -> str:
    choice = (options.format or "auto").lower()
    if choice not in _FORMATS:
        raise KitError(f"--format 只能是 {' / '.join(_FORMATS)}，实际是 {options.format!r}")
    if choice != "auto":
        if choice == "api" and not options.url:
            raise KitError("--format api 需要同时给 --url")
        return choice
    if options.out == "-":
        if options.url:
            return "api"
        return "jsonl"
    suffix = Path(options.out).suffix.lower()
    if suffix in _EXTENSIONS:
        return _EXTENSIONS[suffix]
    if options.url:
        return "api"
    raise KitError(
        f"认不出输出格式：{options.out}",
        hint="用 .db / .csv / .jsonl 结尾，或者显式给 --format sqlite|csv|jsonl|api",
    )


def _extra_faults(options: SeedOptions, spec) -> dict[str, list[FaultSpec]]:
    if not options.inject_fault:
        return {}
    if not options.fault_point:
        raise KitError(
            "用了 --inject-fault 就必须指定 --fault-point",
            hint=f"可用点位：{'、'.join(point.name for point in spec.points)}",
        )
    if options.fault_kind not in FAULT_KINDS:
        raise KitError(f"--fault-kind 只能是 {' / '.join(FAULT_KINDS)}")
    from pre_sales_kit.core.text import parse_window

    start, end = parse_window(options.inject_fault)
    fault = FaultSpec(
        kind=options.fault_kind,
        start=start,
        end=end,
        magnitude=options.fault_magnitude,
        note="命令行注入",
        time_of_day=True,
    )
    extra = {options.fault_point: [fault]}
    validate_fault_points(spec, extra)
    return extra


def _write(run: SeedRun, options: SeedOptions, target_format: str) -> sinks.WriteResult:
    if target_format == "sqlite":
        return sinks.write_sqlite(run.dataset, options.out)
    if target_format == "csv":
        return sinks.write_csv(run.dataset, options.out)
    if target_format == "jsonl":
        if options.out == "-":
            stream: TextIO = __import__("sys").stdout
            return sinks.write_jsonl(run.dataset, stream)
        with Path(options.out).open("w", encoding="utf-8") as handle:
            return sinks.write_jsonl(run.dataset, handle)
    if target_format == "api":
        if not options.url:
            raise KitError("推送接口需要 --url，例如 --url http://127.0.0.1:9000/api/ingest")
        return sinks.post_api(
            run.dataset,
            options.url,
            headers=options.headers,
            batch_size=options.batch_size,
            timeout=options.timeout,
            dry_run=options.dry_run,
        )
    raise KitError(f"未实现的输出格式：{target_format}")


# ---------------- 打印 ----------------


def _print_header(console: Console, run: SeedRun, spec) -> None:
    times = run.dataset.times
    banner(
        console,
        "演示数据生成",
        [
            spec.name,
            display_source(spec.source),
            f"{len(times):,} 个时刻 · {run.dataset.interval:g}s 间隔 · "
            f"{format_duration((times[-1] - times[0]).total_seconds())} 时间窗",
        ],
    )


def _print_result(
    console: Console, run: SeedRun, result: sinks.WriteResult, options: SeedOptions
) -> None:
    section(console, "点位")
    rows: list[list[str | Text]] = []
    for series, stat in zip(run.dataset.series, run.stats, strict=True):
        issues = stat.problems(jump_ratio=_jump_limit(stat.model))
        unit = f" {series.spec.unit}" if series.spec.unit else ""
        rows.append(
            [
                Text(stat.name),
                Text(stat.model, style="bright_black"),
                f"{stat.minimum:g} ~ {stat.maximum:g}{unit}",
                Text(f"{stat.max_step:g}", style="bright_black"),
                Text("通过", style="green") if not issues else Text("有问题", style="red"),
            ]
        )
    print_table(
        console,
        ["点位", "模型", "实际范围", "单步最大", "自检"],
        rows,
        aligns=["left", "left", "right", "right", "left"],
        gap=3,
    )

    if run.applied:
        console.print()
        section(console, "注入异常")
        for name, faults in run.applied.items():
            for fault in faults:
                window = f"{fault.start.strftime('%H:%M')}–{fault.end.strftime('%H:%M')}"
                muted(
                    console,
                    f"{name}  {window}  {fault_label(fault.kind)} "
                    f"{fault.magnitude * 100:+.0f}% 量程",
                    indent=4,
                )

    skipped = _skipped_faults(run)
    if skipped:
        console.print()
        muted(console, "有故障段落在数据时间窗之外，已跳过：", indent=2)
        for name, fault in skipped:
            muted(console, f"{name}  {fault.start.strftime('%H:%M')}–{fault.end.strftime('%H:%M')}", indent=4)

    console.print()
    size = f" · {human_size(result.bytes_written)}" if result.bytes_written else ""
    kind = {
        "sqlite": "SQLite",
        "csv": "CSV",
        "jsonl": "JSONL",
        "api": "HTTP",
    }.get(result.format, result.format)
    muted(
        console,
        f"写入  {result.location} · {kind} · {result.rows:,} 行{size}"
        + (f" · {result.detail}" if result.detail else ""),
        indent=2,
    )
    muted(console, f"随机种子 {options.seed}（同参数可复现）", indent=2)


def _print_verification(console: Console, run: SeedRun) -> int:
    console.print()
    failed = run.failed_points
    if not failed:
        line = f"  {Status.PASS.glyph} "
        console.print(
            f"{line}[green]{len(run.stats)}/{len(run.stats)} 点位物理自检通过[/green]"
            "[bright_black]（无越界、无异常跳变、计数不回退）[/bright_black]"
        )
        return 0
    console.print(f"  {Status.FAIL.glyph} [red]{len(failed)} 个点位未通过物理自检：[/red]")
    for stat in failed:
        muted(
            console,
            f"{stat.name}：{'；'.join(stat.problems(jump_ratio=_jump_limit(stat.model)))}",
            indent=4,
        )
    muted(console, "调大时间常数、降低噪声，或把 events/levels 改平稳一些", indent=4)
    return 3


def _skipped_faults(run: SeedRun) -> list[tuple[str, FaultSpec]]:
    skipped: list[tuple[str, FaultSpec]] = []
    for series in run.dataset.series:
        requested = series.spec.faults
        used = run.applied.get(series.spec.name, [])
        for fault in requested:
            if fault not in used:
                skipped.append((series.spec.name, fault))
    return skipped


def _print_path(console: Console, label: str, path: str) -> None:
    from pre_sales_kit.core.console import detail_path

    detail_path(console, label, path)
    console.print()


def print_models(console: Console) -> None:
    """列出五种物理模型及其参数：kit seed --list-models。"""
    from pre_sales_kit.seed.generators import MODEL_DOCS

    console.print()
    console.print("  五种物理模型（曲线形状由物理决定，随机只决定细节）")
    for name in sorted(MODEL_DOCS):
        description, params = MODEL_DOCS[name]
        console.print()
        console.print(f"  [bold]{name}[/bold]  [bright_black]{description}[/bright_black]")
        for key, note in params.items():
            console.print(f"      [cyan]{key}[/cyan]  [bright_black]{note}[/bright_black]")
    console.print()
    console.print("  [bright_black]用量程硬约束兜底：任何时刻都不越界（range 是死规定）[/bright_black]")
    console.print()


def _print_error(console: Console, exc: KitError) -> None:
    from pre_sales_kit.core.console import error

    error(console, str(exc), exc.hint)
