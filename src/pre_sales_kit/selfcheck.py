"""安装自检：确认「装上了」和「装对了」是同一件事。

不要小看这一条：现场最耗时间的失败不是工具报错，
而是 `kit` 命令指向了另一个 Python 环境里的旧版本——
它照样能跑，只是行为跟文档对不上，然后你在会议室里怀疑人生。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path

from rich.console import Console

from pre_sales_kit import __version__
from pre_sales_kit.core.console import (
    hint_line,
    make_console,
    section,
)
from pre_sales_kit.core.proc import IS_WINDOWS, platform_summary, python_version
from pre_sales_kit.core.status import Status

#: 与 pyproject.toml 的 requires-python 保持一致
MIN_PYTHON = (3, 11)

#: 必须装的运行依赖（写死版本下限，和 pyproject 对齐）
REQUIRED: dict[str, str] = {
    "typer": "0.15",
    "rich": "13.9",
    "pyyaml": "6.0",
    "jinja2": "3.1",
}
WINDOWS_ONLY: dict[str, str] = {"tzdata": "2024.1"}

#: 报告与预览模板（少了就是打不完整包，报告会报错）
TEMPLATES = (
    "templates/doctor_report.html.j2",
    "templates/seed_preview.html.j2",
    "templates/compat_matrix.html.j2",
    "templates/_report.css",
)
SCAFFOLD = ("doctor.yaml", "points.yaml", "matrix.yaml")

DIST_NAME = "pre-sales-kit"


@dataclass(frozen=True)
class Check:
    key: str
    name: str
    status: Status
    detail: str
    hint: str = ""


def _ver_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _at_least(version: str, minimum: str) -> bool:
    left, right = _ver_tuple(version), _ver_tuple(minimum)
    length = max(len(left), len(right))
    return left + (0,) * (length - len(left)) >= right + (0,) * (length - len(right))


def check_python() -> Check:
    version = python_version()
    need = ".".join(str(part) for part in MIN_PYTHON)
    if sys.version_info[:2] >= MIN_PYTHON:
        return Check("python", "Python 版本", Status.PASS, f"{version} · 需要 ≥ {need}")
    return Check(
        "python",
        "Python 版本",
        Status.FAIL,
        f"{version} · 需要 ≥ {need}",
        hint=f"这个版本跑不起来；装 {need}+ 后：pipx install --python {need} pre-sales-kit",
    )


def check_packages() -> list[Check]:
    checks: list[Check] = []
    wanted = dict(REQUIRED)
    if IS_WINDOWS:
        wanted.update(WINDOWS_ONLY)
    for name, minimum in wanted.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            checks.append(
                Check(
                    f"dep-{name}",
                    f"依赖 {name}",
                    Status.FAIL,
                    "未安装",
                    hint=f"pip install {DIST_NAME}（依赖会一起装上）；已经装了就直接 --force-reinstall",
                )
            )
            continue
        if _at_least(installed, minimum):
            checks.append(Check(f"dep-{name}", f"依赖 {name}", Status.PASS, f"{installed} · ≥ {minimum}"))
        else:
            checks.append(
                Check(
                    f"dep-{name}",
                    f"依赖 {name}",
                    Status.FAIL,
                    f"{installed} · 低于要求的 {minimum}",
                    hint=f"pip install -U \"{name}\">={minimum}",
                )
            )
    return checks


def check_distribution() -> Check:
    try:
        installed = metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        return Check(
            "dist",
            "安装记录",
            Status.WARN,
            f"没查到 {DIST_NAME} 的安装元数据（可能是在源码目录里直接跑的）",
            hint="用 pip install -e . 装一次，命令才会出现在 PATH 里",
        )
    if installed == __version__:
        return Check("dist", "安装记录", Status.PASS, f"{DIST_NAME} {installed}")
    return Check(
        "dist",
        "安装记录",
        Status.WARN,
        f"元数据是 {installed}，代码是 {__version__}（旧版本文件没清干净？）",
        hint=f"pip install --force-reinstall {DIST_NAME}",
    )


def check_data_files() -> list[Check]:
    checks: list[Check] = []
    missing_templates = [name for name in TEMPLATES if not _resource_exists("pre_sales_kit", name)]
    if missing_templates:
        checks.append(
            Check(
                "templates",
                "报告模板",
                Status.FAIL,
                f"缺 {len(missing_templates)} 个：{'、'.join(missing_templates)}",
                hint="安装包不完整，--html 出报告时会失败；重新安装",
            )
        )
    else:
        checks.append(Check("templates", "报告模板", Status.PASS, f"{len(TEMPLATES)} 个模板齐全"))

    missing_scaffold = [name for name in SCAFFOLD if not _resource_exists("pre_sales_kit.scaffold", name)]
    if missing_scaffold:
        checks.append(
            Check(
                "scaffold",
                "示例文件",
                Status.FAIL,
                f"缺 {'、'.join(missing_scaffold)}",
                hint="kit init 会失败；重新安装 pre-sales-kit",
            )
        )
    else:
        checks.append(Check("scaffold", "示例文件", Status.PASS, f"{len(SCAFFOLD)} 个示例齐全（kit init 可用）"))
    return checks


def _resource_exists(package: str, name: str) -> bool:
    try:
        node = resources.files(package)
    except (ModuleNotFoundError, TypeError):
        return False
    for part in name.split("/"):
        node = node.joinpath(part)

    try:
        return bool(node.is_file())
    except (OSError, ValueError):
        return False


def check_entry() -> Check:
    kit = shutil.which("kit")
    pskit = shutil.which("pskit")
    if kit:
        detail = kit
        if pskit and pskit != kit:
            detail += f" · 别名 pskit 指向 {pskit}"
        return Check("entry", "命令行入口", Status.PASS, detail)
    return Check(
        "entry",
        "命令行入口",
        Status.WARN,
        "PATH 里找不到 kit",
        hint=f"仍可用 python -m pre_sales_kit 跑；要拿到 kit 命令，把 Scripts 目录加进 PATH 或 pipx install {DIST_NAME}",
    )


def check_encoding() -> Check:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if encoding.startswith("utf"):
        return Check("encoding", "控制台编码", Status.PASS, f"stdout 是 {encoding}")
    return Check(
        "encoding",
        "控制台编码",
        Status.WARN,
        f"stdout 是 {encoding or '未知'}，中文可能显示成问号",
        hint="Windows 上执行 chcp 65001，或设 PYTHONIOENCODING=utf-8；报告 HTML 不受影响",
    )


def check_write(directory: Path) -> Check:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check(
            "write",
            "输出目录可写",
            Status.FAIL,
            f"{directory} 建不出来：{exc}",
            hint="换一个目录（例如 --dir out），或换个有写权限的位置",
        )
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".kit-write-", suffix=".tmp", delete=True):
            pass
    except OSError as exc:
        return Check(
            "write",
            "输出目录可写",
            Status.FAIL,
            f"{directory} 写不进去：{exc}",
            hint="报告和演示数据都要写盘；换目录或检查权限 / 杀毒软件拦截",
        )
    return Check("write", "输出目录可写", Status.PASS, str(directory))


def run_checks(*, directory: Path | None = None) -> list[Check]:
    target = directory or Path.cwd()
    checks: list[Check] = [check_python()]
    checks.extend(check_packages())
    checks.append(check_distribution())
    checks.extend(check_data_files())
    checks.append(check_entry())
    checks.append(check_encoding())
    checks.append(check_write(target))
    return checks


def to_dict(checks: list[Check]) -> dict[str, object]:
    return {
        "version": __version__,
        "python": python_version(),
        "platform": platform_summary(),
        "checks": [
            {
                "key": check.key,
                "name": check.name,
                "status": check.status.value,
                "detail": check.detail,
                "hint": check.hint,
            }
            for check in checks
        ],
        "failed": sum(1 for check in checks if check.status is Status.FAIL),
        "warned": sum(1 for check in checks if check.status is Status.WARN),
    }


def exit_code(checks: list[Check]) -> int:
    return 1 if any(check.status is Status.FAIL for check in checks) else 0


def print_checks(console: Console, checks: list[Check], *, started: float) -> None:
    from pre_sales_kit.core.console import check_line, summary, verdict

    section(console, "安装")
    for check in checks:
        check_line(console, check.status, check.name, check.detail, label_width=20)
        if check.hint and check.status is not Status.PASS:
            hint_line(console, f"→ {check.hint}", indent=8)
    counts = {status: 0 for status in Status}
    for check in checks:
        counts[check.status] += 1
    duration = time.perf_counter() - started
    console.print()
    summary(console, counts, duration)
    failed = [check for check in checks if check.status is Status.FAIL]
    warned = [check for check in checks if check.status is Status.WARN]
    if failed:
        verdict(console, Status.FAIL, f"装得不对：{failed[0].name} 没通过，先处理它")
    elif warned:
        verdict(console, Status.WARN, f"可以用，但有 {len(warned)} 项要留意（不影响今天用）")
    else:
        verdict(console, Status.PASS, "装好了，可以直接开始：kit init")


def dumps(checks: list[Check]) -> str:
    return json.dumps(to_dict(checks), ensure_ascii=False, indent=2)


__all__ = [
    "Check",
    "check_data_files",
    "check_packages",
    "dumps",
    "exit_code",
    "make_console",
    "print_checks",
    "run_checks",
    "to_dict",
]
