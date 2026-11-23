"""kit 命令行入口：doctor / seed / compat 三个子命令。

设计原则：命令本身只说「我做了什么」，判断逻辑全在各自的模块里。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from pre_sales_kit import __version__
from pre_sales_kit.core.console import error, make_console
from pre_sales_kit.core.errors import KitError
from pre_sales_kit.doctor.present import (
    Grouper,
    print_banner,
    print_fixes,
    print_footer,
    print_report,
    print_result,
)
from pre_sales_kit.doctor.report import write_report
from pre_sales_kit.doctor.runner import DoctorRunner
from pre_sales_kit.doctor.spec import load_spec

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="售前现场工具箱：环境自检、演示数据、兼容性矩阵，外加示例脚手架与演示服务。",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pre-sales-kit {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="显示版本号"),
    ] = False,
) -> None:
    """售前现场工具箱。

    \b
    六个子命令：
      kit init       导出可运行的示例（装完第一步）
      kit selfcheck  确认装对了：Python、依赖、模板、命令行入口
      kit doctor     演示环境自检（出门前跑一遍）
      kit demo       演示服务：起、停、查、开
      kit seed       生成物理合理的演示数据
      kit compat     兼容性矩阵查询
    """


@app.command("doctor")
def doctor_command(
    spec: Annotated[
        Path,
        typer.Argument(
            help="体检声明文件（YAML）",
            show_default=True,
        ),
    ] = Path("doctor.yaml"),
    fix: Annotated[bool, typer.Option("--fix", help="尝试自动修复失败的检查项")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="允许执行高风险修复动作")] = False,
    html: Annotated[
        Path | None, typer.Option("--html", help="把报告写到这个 HTML 文件（单文件、无外部依赖）")
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="输出 JSON（给脚本 / CI 用）")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="把警告也当作失败（退出码 1）")] = False,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="只跑带这个标签（或 id）的检查，可重复")
    ] = None,
    skip: Annotated[
        list[str] | None, typer.Option("--skip", help="跳过带这个标签（或 id）的检查，可重复")
    ] = None,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="输出更多细节")] = False,
    list_types: Annotated[
        bool, typer.Option("--list-types", help="列出所有内置检查类型后退出（不需要声明文件）")
    ] = False,
) -> None:
    """演示环境自检：检查端口、服务、数据、环境、版本、网络、显示。

    \b
    示例：
      kit doctor                          # 用当前目录的 doctor.yaml
      kit doctor examples/doctor.yaml      # 指定声明文件
      kit doctor --fix                     # 能修的自动修，修完复检
      kit doctor --html out/report.html    # 同时生成可转发的单文件报告
    """
    console = make_console(no_color=no_color)
    if list_types:
        _print_types(console)
        raise typer.Exit(code=0)

    try:
        loaded = load_spec(spec)
    except KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    runner = DoctorRunner(loaded, verbose=verbose)
    started = runner.context.now.strftime("%Y-%m-%d %H:%M:%S")
    stream = not json_out and not fix
    grouper = Grouper(console)

    if stream and not json_out:
        host = runner.host_info()
        print_banner(
            console,
            loaded,
            started,
            f"{host['平台']} · Python {host.get('Python', '')}",
        )

    def on_progress(result) -> None:
        print_result(console, result, grouper)

    try:
        report = runner.run(
            only=only or (),
            skip=skip or (),
            progress=on_progress if stream else None,
        )
        if fix:
            runner.fix(report, yes=yes)
        report.finished_at = report.finished_at if not fix else _now()
    except KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    html_path: Path | None = None
    if html is not None:
        html_path = write_report(report, html)

    if json_out:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(code=report.exit_code(strict=strict))

    if fix:
        print_report(console, report)
        print_fixes(console, report.fixes)
    else:
        print_fixes(console, report.fixes)
        from pre_sales_kit.core.console import summary, verdict

        summary(console, report.counts, report.duration)
        verdict(console, report.overall, report.verdict_text())

    print_footer(console, html_path=html_path, show_html_hint=not verbose)
    raise typer.Exit(code=report.exit_code(strict=strict))


def _now():
    from datetime import datetime

    return datetime.now().astimezone()


def _print_types(console) -> None:
    from rich.table import Table

    from pre_sales_kit.doctor.registry import describe, known_types, load_builtin_checks

    load_builtin_checks()
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("类型")
    table.add_column("说明")
    for name in known_types():
        table.add_row(name, describe(name))
    console.print()
    console.print("  内置检查类型")
    console.print()
    console.print(table)


@app.command("init")
def init_command(
    directory: Annotated[
        Path, typer.Argument(help="把示例写到哪个目录（默认 ./examples）")
    ] = Path("examples"),
    force: Annotated[
        bool, typer.Option("--force", "-f", help="覆盖已存在的同名文件（先备份成 .bak）")
    ] = False,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="只导出指定文件，可重复")
    ] = None,
    list_files: Annotated[
        bool, typer.Option("--list", help="只列出可导出的示例，不写文件")
    ] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """导出三份可运行的示例声明：doctor.yaml / points.yaml / matrix.yaml。

    \b
    装完 kit 之后先跑这个：pip 装出来的人手里没有仓库里的 examples/，
    而后面所有命令都靠这三份声明起步。

    已存在的文件默认不覆盖——现场机器上的 YAML 可能已经被改过，那才是真的资产。

    \b
    示例：
      kit init                       # 写到 ./examples
      kit init demo --only doctor.yaml
      kit init --force               # 覆盖，原文件备份为 .bak
    """
    from pre_sales_kit import scaffold
    from pre_sales_kit.core.console import banner, check_line, hint_line, section
    from pre_sales_kit.core.status import Status

    console = make_console(no_color=no_color)

    if list_files:
        from rich.table import Table

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("文件")
        table.add_column("说明")
        for name in scaffold.FILES:
            table.add_row(name, scaffold.DESCRIPTION.get(name, ""))
        console.print()
        console.print("  可导出的示例")
        console.print()
        console.print(table)
        raise typer.Exit(code=0)

    try:
        results = scaffold.export(directory, names=only, force=force)
    except KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    banner(
        console,
        "示例脚手架",
        [
            f"目标目录 {Path(directory).resolve()}",
            f"pre-sales-kit {__version__}",
        ],
    )

    status_of = {
        "created": (Status.PASS, "新建"),
        "overwritten": (Status.WARN, "覆盖（原文件已备份）"),
        "identical": (Status.SKIP, "已是最新"),
        "skipped": (Status.WARN, "保留原文件"),
    }
    section(console, "写入")
    for result in results:
        status, word = status_of.get(result.action, (Status.PASS, result.action))
        detail = f"{word} · {result.detail}" if result.detail else word
        check_line(console, status, result.name, detail, label_width=18)

    written = sum(1 for result in results if result.written)
    console.print()
    if written == 0 and any(result.action == "skipped" for result in results):
        hint_line(
            console,
            "有文件没有写入（内容和你手上的不同）。要拿最新样板就加 --force，原文件会备份成 .bak。",
        )
        console.print()

    section(console, "下一步")
    for command, note in scaffold.next_steps(directory):
        hint_line(console, f"$ {command}", indent=4)
        hint_line(console, note, indent=8)
    console.print()
    raise typer.Exit(code=0)


compat_app = typer.Typer(help="兼容性矩阵查询。", no_args_is_help=True)
app.add_typer(compat_app, name="compat")


@app.command("seed")
def seed_command(
    model: Annotated[
        Path, typer.Option("--model", "-m", help="点位模型 YAML（写清 model / range / params）")
    ] = Path("points.yaml"),
    out: Annotated[
        str,
        typer.Option("--out", "-o", help="输出：demo.db / demo.csv / demo.jsonl / -（标准输出）"),
    ] = "demo.db",
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="sqlite / csv / jsonl / api；默认按扩展名推断")
    ] = "auto",
    start: Annotated[
        str, typer.Option("--start", help="开始时间：now / -24h / 2026-11-23 08:00 / 14:20")
    ] = "-24h",
    end: Annotated[str, typer.Option("--end", help="结束时间")] = "now",
    interval: Annotated[
        str | None, typer.Option("--interval", help="采样间隔，如 5s / 1m / 500ms")
    ] = None,
    seed: Annotated[int, typer.Option("--seed", help="随机种子：同参数可复现同一份数据")] = 42,
    url: Annotated[
        str | None, typer.Option("--url", help="--format api 时的推送地址")
    ] = None,
    header: Annotated[
        list[str] | None,
        typer.Option("--header", help='推送时附带的请求头，格式 "Key: Value"，可重复'),
    ] = None,
    batch_size: Annotated[int, typer.Option("--batch-size", help="推送每批条数")] = 500,
    timeout: Annotated[float, typer.Option("--timeout", help="推送单次超时（秒）")] = 10.0,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只打印请求体，不真的推送")] = False,
    html: Annotated[
        Path | None, typer.Option("--html", help="生成数据预览 HTML（曲线 + 自检结果）")
    ] = None,
    inject_fault: Annotated[
        str | None, typer.Option("--inject-fault", help="注入异常的时间窗，例如 14:20-14:35")
    ] = None,
    fault_point: Annotated[
        str | None, typer.Option("--fault-point", help="在哪条点位上注入异常")
    ] = None,
    fault_kind: Annotated[
        str,
        typer.Option(
            "--fault-kind", help="异常形态：ramp（缓升）/ spike（尖峰）/ dropout（卡死）/ drift（漂移）"
        ),
    ] = "ramp",
    fault_magnitude: Annotated[
        float, typer.Option("--fault-magnitude", help="异常幅度（相对量程的比例，0.23 = +23%）")
    ] = 0.23,
    verify: Annotated[
        bool, typer.Option("--verify/--no-verify", help="生成后做物理自检")
    ] = True,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="只输出结果，不打印过程")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
    list_models: Annotated[
        bool, typer.Option("--list-models", help="列出五种物理模型及参数后退出")
    ] = False,
) -> None:
    """生成物理上说得通的演示数据（温度不会瞬跳、功率因数不会越界）。

    \b
    示例：
      kit seed -m examples/points.yaml -o examples/data/demo.db
      kit seed -m points.yaml -o demo.csv --start -8h --interval 1s
      kit seed -m points.yaml -o - --format jsonl | head
      kit seed -m points.yaml -o demo.db --inject-fault 14:20-14:35 \\
          --fault-point 主轴电流 --fault-magnitude 0.23
    """
    from pre_sales_kit.seed.pipeline import SeedOptions, execute, print_models

    console = make_console(no_color=no_color)
    if list_models:
        print_models(console)
        raise typer.Exit(code=0)

    headers: dict[str, str] = {}
    for raw in header or []:
        if ":" not in raw:
            error(console, f"--header 格式不对：{raw!r}", '应该写成 "Key: Value"')
            raise typer.Exit(code=2)
        key, value = raw.split(":", 1)
        headers[key.strip()] = value.strip()

    options = SeedOptions(
        model=model,
        out=out,
        format=output_format,
        start=start,
        end=end,
        interval=interval,
        seed=seed,
        url=url,
        headers=headers,
        batch_size=batch_size,
        timeout=timeout,
        dry_run=dry_run,
        html=html,
        inject_fault=inject_fault,
        fault_point=fault_point,
        fault_kind=fault_kind,
        fault_magnitude=fault_magnitude,
        verify=verify,
        quiet=quiet,
        console=console,
    )
    raise typer.Exit(code=execute(options))


@compat_app.command("check")
def compat_check(
    matrix_path: Annotated[
        Path, typer.Option("--matrix", "-m", help="兼容性矩阵文件（YAML）")
    ] = Path("matrix.yaml"),
    query_os: Annotated[str | None, typer.Option("--os", help="操作系统，例如 \"Windows Server 2016\"")] = None,
    query_db: Annotated[str | None, typer.Option("--db", help="数据库，例如 \"Oracle 11g\"")] = None,
    query_browser: Annotated[str | None, typer.Option("--browser", help="浏览器")] = None,
    query_middleware: Annotated[str | None, typer.Option("--middleware", help="中间件")] = None,
    set_values: Annotated[
        list[str] | None,
        typer.Option("--set", help="任意维度：--set cpu=鲲鹏920，可重复"),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="输出 JSON")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """查一个组合的支持情况，并给出投标口径的结论。

    \b
    示例：
      kit compat check --os "Windows Server 2016" --db "Oracle 11g"
      kit compat check --os centos --db "达梦" --json
    """
    from pre_sales_kit.compat.query import evaluate
    from pre_sales_kit.compat.render import print_verdict, verdict_json
    from pre_sales_kit.compat.spec import load_matrix

    console = make_console(no_color=no_color)
    query = _compat_query(
        query_os, query_db, query_browser, query_middleware, set_values or [], console
    )
    if not query:
        error(console, "至少要指定一个维度", '例如：kit compat check --os "Windows Server 2019" --db "Oracle 19c"')
        raise typer.Exit(code=2)
    try:
        matrix = load_matrix(matrix_path)
    except KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    verdict = evaluate(matrix, query)
    if json_out:
        typer.echo(verdict_json(verdict, matrix))
    else:
        print_verdict(console, verdict, matrix)
    raise typer.Exit(code=verdict.exit_code)


@compat_app.command("matrix")
def compat_matrix(
    matrix_path: Annotated[
        Path, typer.Option("--matrix", "-m", help="兼容性矩阵文件（YAML）")
    ] = Path("matrix.yaml"),
    html: Annotated[Path | None, typer.Option("--html", help="生成可贴进方案书的 HTML 表格")] = None,
    md: Annotated[Path | None, typer.Option("--md", help="生成 Markdown 表格")] = None,
    brand: Annotated[str | None, typer.Option("--brand", help="页眉里的公司 / 项目名")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="输出 JSON")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """打印整张兼容性矩阵，或导出成 HTML / Markdown。

    \b
    示例：
      kit compat matrix
      kit compat matrix --html out/matrix.html --brand "XX 科技"
      kit compat matrix --md out/matrix.md
    """
    import json as json_module

    from pre_sales_kit.compat.render import print_matrix, write_html, write_markdown
    from pre_sales_kit.compat.spec import load_matrix
    from pre_sales_kit.core.console import detail_path

    console = make_console(no_color=no_color)
    try:
        matrix = load_matrix(matrix_path)
    except KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    if json_out:
        payload = {
            "product": matrix.product,
            "updated": matrix.updated,
            "dimensions": {
                dimension.key: [
                    {"name": item.name, "status": item.level, "note": item.note}
                    for item in dimension.items
                ]
                for dimension in matrix.dimensions
            },
        }
        typer.echo(json_module.dumps(payload, ensure_ascii=False, indent=2))
        raise typer.Exit(code=0)

    if html is None and md is None:
        print_matrix(console, matrix)
        raise typer.Exit(code=0)

    if html is not None:
        path = write_html(matrix, html, brand=brand)
        detail_path(console, "HTML", str(path))
    if md is not None:
        path = write_markdown(matrix, md, brand=brand)
        detail_path(console, "Markdown", str(path))
    console.print()
    raise typer.Exit(code=0)


def _compat_query(
    query_os: str | None,
    query_db: str | None,
    query_browser: str | None,
    query_middleware: str | None,
    set_values: list[str],
    console,
) -> dict[str, str]:
    query: dict[str, str] = {}
    for key, value in (
        ("os", query_os),
        ("db", query_db),
        ("browser", query_browser),
        ("middleware", query_middleware),
    ):
        if value:
            query[key] = value
    for raw in set_values:
        if "=" not in raw:
            error(console, f"--set 的格式是 键=值，实际是 {raw!r}")
            raise typer.Exit(code=2)
        key, value = raw.split("=", 1)
        query[key.strip()] = value.strip()
    return query


demo_app = typer.Typer(
    help="演示服务：一键起、停、查、开。只碰自己起的进程。",
    no_args_is_help=True,
)
app.add_typer(demo_app, name="demo")


def _parse_services(raw: list[str] | None) -> list[tuple[str, int]]:
    """--service 的解析：portal=8080 / 8080（默认角色 portal）/ alarm=6379。"""
    from pre_sales_kit.core.errors import KitError as _KitError
    from pre_sales_kit.demo import manager

    if not raw:
        return list(manager.DEFAULT_SERVICES)
    targets: list[tuple[str, int]] = []
    for item in raw:
        role, _, port_text = item.partition("=")
        if not port_text:
            role, port_text = "portal", item
        try:
            port = int(port_text)
        except ValueError as exc:
            raise _KitError(
                f"--service 的端口不是数字：{item!r}",
                hint='写成 "portal=8080"，或只写端口 8080（默认角色是 portal）',
            ) from exc
        if not 1 <= port <= 65535:
            raise _KitError(f"端口超出范围：{port}", hint="1-65535 之间")
        targets.append((role.strip() or "portal", port))
    return targets


def _print_outcomes(console, outcomes, *, title: str) -> int:
    """打印一批动作结果，返回应该用的退出码。"""
    from pre_sales_kit.core.console import check_line, section
    from pre_sales_kit.core.status import Status

    status_of = {
        "started": Status.PASS,
        "running": Status.SKIP,
        "stopped": Status.PASS,
        "gone": Status.SKIP,
        "skipped": Status.WARN,
        "failed": Status.FAIL,
    }
    section(console, title)
    for outcome in outcomes:
        port = f":{outcome.port}" if outcome.port else ""
        check_line(
            console,
            status_of.get(outcome.status, Status.WARN),
            f"{outcome.label}{port}",
            outcome.detail,
            label_width=22,
        )
    console.print()
    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


@demo_app.command("up")
def demo_up(
    service: Annotated[
        list[str] | None,
        typer.Option(
            "--service",
            help='角色=端口，可重复；默认 "portal=8080" 与 "alarm=6379"',
        ),
    ] = None,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="只起这些角色，可重复")
    ] = None,
    db: Annotated[
        str | None, typer.Option("--db", help="演示数据库路径（默认 data/demo.db）")
    ] = None,
    wait: Annotated[float, typer.Option("--wait", help="每个服务最多等多少秒起来")] = 5.0,
    directory: Annotated[
        Path, typer.Option("--dir", help="状态文件放在这个目录的 .kit/ 下")
    ] = Path(),
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """后台拉起演示服务，并把 PID 记进 .kit/demo.json。

    \b
    端口已经被别的进程占着时**不会去杀它**，只报告——
    客户机器上的 8080 可能是他们真实的服务。

    \b
    示例：
      kit demo up
      kit demo up --only portal
      kit demo up --service portal=8090 --service alarm=6380
    """
    from pre_sales_kit.core.errors import KitError as _KitError
    from pre_sales_kit.demo import manager

    console = make_console(no_color=no_color)
    try:
        targets = _parse_services(service)
    except _KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc
    if only:
        wanted = set(only)
        targets = [item for item in targets if item[0] in wanted]
        if not targets:
            error(console, "筛选后没有要起的服务", "检查 --only 的角色名是不是 portal / alarm")
            raise typer.Exit(code=2)

    root = directory.resolve()
    try:
        outcomes = manager.start(root, targets, db=db, wait=wait)
    except _KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    code = _print_outcomes(console, outcomes, title="启动")
    if code == 0:
        from pre_sales_kit.core.console import hint_line

        port = next((item.port for item in outcomes if item.role == "portal" and item.port), None)
        if port:
            hint_line(console, f"打开看看：http://127.0.0.1:{port}/   （或 kit demo open）")
        hint_line(console, "停掉：kit demo down")
        console.print()
    raise typer.Exit(code=code)


@demo_app.command("down")
def demo_down(
    only: Annotated[
        list[str] | None, typer.Option("--only", help="只停这些角色，可重复")
    ] = None,
    directory: Annotated[
        Path, typer.Option("--dir", help="状态文件放在这个目录的 .kit/ 下")
    ] = Path(),
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """停掉 kit 起过的演示服务。

    \b
    记录里的 PID 已经换成了别的进程时，只清记录、不动手——防止误杀。
    """
    from pre_sales_kit.core.errors import KitError as _KitError
    from pre_sales_kit.demo import manager

    console = make_console(no_color=no_color)
    try:
        outcomes = manager.stop(directory.resolve(), roles=only)
    except _KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc
    if not outcomes:
        from pre_sales_kit.core.console import hint_line

        hint_line(console, "没有需要停的服务（.kit/demo.json 里是空的）", indent=2)
        console.print()
        raise typer.Exit(code=0)
    raise typer.Exit(code=_print_outcomes(console, outcomes, title="停止"))


@demo_app.command("status")
def demo_status(
    directory: Annotated[
        Path, typer.Option("--dir", help="状态文件放在这个目录的 .kit/ 下")
    ] = Path(),
    json_out: Annotated[bool, typer.Option("--json", help="输出 JSON")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """看演示服务现在是什么状态：在跑、掉了，还是端口被别人占了。"""
    from pre_sales_kit.core.errors import KitError as _KitError
    from pre_sales_kit.demo import manager

    console = make_console(no_color=no_color)
    try:
        rows, hints = manager.status(directory.resolve())
    except _KitError as exc:
        error(console, str(exc), exc.hint)
        raise typer.Exit(code=2) from exc

    if json_out:
        payload = {
            "services": [
                {
                    "role": row.record.role,
                    "port": row.record.port,
                    "pid": row.record.pid,
                    "state": row.state,
                    "detail": row.detail,
                    "started": row.record.started,
                }
                for row in rows
            ],
            "hints": hints,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        raise typer.Exit(code=0)

    from pre_sales_kit.core.console import check_line, hint_line, section
    from pre_sales_kit.core.status import Status

    status_of = {
        "running": Status.PASS,
        "starting": Status.WARN,
        "dead": Status.FAIL,
        "taken": Status.WARN,
        "unverified": Status.WARN,
    }
    if not rows:
        hint_line(console, "没有记录。起服务：kit demo up", indent=2)
    else:
        section(console, "演示服务")
        for row in rows:
            check_line(
                console,
                status_of.get(row.state, Status.WARN),
                f"{row.record.label}:{row.record.port}",
                row.detail,
                label_width=24,
            )
    for hint in hints:
        hint_line(console, f"· {hint}", indent=4)
    console.print()
    raise typer.Exit(code=0)


@demo_app.command("open")
def demo_open(
    port: Annotated[
        int | None, typer.Option("--port", help="要打开的端口（默认用状态里的门户端口）")
    ] = None,
    directory: Annotated[
        Path, typer.Option("--dir", help="状态文件放在这个目录的 .kit/ 下")
    ] = Path(),
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """用默认浏览器打开演示门户。"""
    from pre_sales_kit.demo import manager

    console = make_console(no_color=no_color)
    target = port or manager.guess_port(directory.resolve())
    opened = manager.open_port(target)
    from pre_sales_kit.core.console import hint_line

    if opened:
        hint_line(console, f"已打开 http://127.0.0.1:{target}/", indent=2)
    else:
        error(console, "没能拉起浏览器", f"手动打开 http://127.0.0.1:{target}/")
        console.print()
        raise typer.Exit(code=1)
    console.print()
    raise typer.Exit(code=0)


@app.command("selfcheck")
def selfcheck_command(
    json_out: Annotated[bool, typer.Option("--json", help="输出 JSON（给脚本用）")] = False,
    directory: Annotated[
        Path, typer.Option("--dir", help="检查这个目录能否写报告和演示数据")
    ] = Path(),
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭颜色")] = False,
) -> None:
    """安装自检：Python 版本、依赖、报告模板、示例文件、命令行入口。

    \b
    装完立刻跑一次。它回答的问题不是「装了吗」，而是「装对了吗」——
    包括 PATH 里的 kit 是不是新版本、模板有没有漏装。

    \b
    示例：
      kit selfcheck
      kit selfcheck --json
    """
    import time as _time

    from pre_sales_kit import selfcheck
    from pre_sales_kit.core.console import banner

    console = make_console(no_color=no_color)
    started = _time.perf_counter()
    checks = selfcheck.run_checks(directory=directory)

    if json_out:
        typer.echo(selfcheck.dumps(checks))
        raise typer.Exit(code=selfcheck.exit_code(checks))

    from pre_sales_kit.core.proc import platform_summary, python_version

    banner(
        console,
        "安装自检",
        [
            f"pre-sales-kit {__version__} · Python {python_version()}",
            f"{platform_summary()} · {Path.cwd()}",
        ],
    )
    selfcheck.print_checks(console, checks, started=started)
    raise typer.Exit(code=selfcheck.exit_code(checks))


def main() -> None:
    app()


if __name__ == "__main__":
    main()