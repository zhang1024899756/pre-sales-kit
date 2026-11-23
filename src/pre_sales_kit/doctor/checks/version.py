"""版本检查：关键工具 / 依赖的版本是否满足声明。

约束写法（可逗号组合）：
    ">=3.11"   ">=20,<25"   "==3.11.2"   "~=1.2"   "^3.11"   "3.11"（前缀匹配）
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from pre_sales_kit.core.proc import run_capture, which
from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = ("tools", "python_packages", "timeout")

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_INTERPRETERS = {"python", "python3", "python.exe"}


@check("version", "版本：外部工具与 Python 依赖是否满足声明")
def run_version(spec: CheckSpec, ctx: CheckContext) -> list[Item]:  # noqa: ARG001 - ctx 是检查协议的一部分
    params = Params.of(spec, _ALLOWED)
    tools = params.mapping("tools")
    packages = params.mapping("python_packages")
    timeout = params.number("timeout", 20.0) or 20.0
    if not tools and not packages:
        from pre_sales_kit.core.errors import SpecError

        raise SpecError(
            message=(
                f"检查「{spec.name}」（{spec.id}）的 version 没有配置任何一项；"
                "至少写 tools 或 python_packages"
            ),
            path=spec.source,
        )

    items: list[Item] = []
    for name, constraint in tools.items():
        items.append(_check_tool(str(name), str(constraint), timeout))
    for package, constraint in packages.items():
        items.append(_check_package(str(package), str(constraint)))
    return items


def _check_tool(name: str, constraint: str, timeout: float) -> Item:
    key = name.strip().lower()
    if key in _INTERPRETERS:
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        return _verdict(name, version, constraint)
    executable = which(name)
    if not executable:
        path = Path(name)
        if path.exists():
            executable = str(path)
        else:
            return Item(
                name,
                Status.FAIL,
                f"PATH 里找不到 {name}",
                hint="先装上，或把它的目录加进 PATH；也可以写成绝对路径",
            )
    try:
        _code, output = run_capture([executable, "--version"], timeout=timeout)
    except Exception as exc:
        return Item(name, Status.WARN, f"执行 --version 失败：{exc}")
    detected = _first_version(output)
    if detected is None:
        preview = " ".join(output.split())[:60] or "（无输出）"
        return Item(name, Status.WARN, f"认不出版本号：{preview}")
    return _verdict(name, detected, constraint)


def _check_package(package: str, constraint: str) -> Item:
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError:
        return Item(
            package,
            Status.FAIL,
            "未安装",
            hint=f"pip install {package}",
        )
    return _verdict(package, version, constraint)


def _verdict(name: str, version: str, constraint: str) -> Item:
    number = _version_tuple(version)
    if number is None:
        return Item(name, Status.WARN, f"版本号无法比较：{version!r}")
    try:
        satisfied = satisfies(number, constraint)
    except ValueError as exc:
        return Item(name, Status.WARN, f"约束 {constraint!r} 写错了：{exc}")
    if satisfied:
        return Item(name, Status.PASS, f"{version} · 满足 {constraint}")
    return Item(
        name,
        Status.FAIL,
        f"{version} · 不满足 {constraint}",
        hint="版本不符时演示可能出现诡异问题；先升级或改用兼容版本",
    )


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def _first_version(text: str) -> str | None:
    match = _VERSION_RE.search(text)
    return match.group(0) if match else None


_OPS = (">=", "<=", "==", "!=", "~=", ">", "<", "=", "^")


def satisfies(version: tuple[int, ...], constraint: str) -> bool:
    """判断版本是否满足约束；支持逗号分隔的多个子句（全部满足才算）。"""
    for clause in (part.strip() for part in constraint.split(",")):
        if not clause:
            continue
        if not _clause_ok(version, clause):
            return False
    return True


def _clause_ok(version: tuple[int, ...], clause: str) -> bool:
    op = ""
    rest = clause
    for candidate in _OPS:
        if clause.startswith(candidate):
            op = candidate
            rest = clause[len(candidate) :].strip()
            break
    target = _version_tuple(rest)
    if target is None:
        raise ValueError(f"版本号部分不是数字：{rest!r}")
    left = _pad(version, len(target))
    right = target
    if op in (">=",):
        return left >= right
    if op in ("<=",):
        return left <= right
    if op in (">",):
        return left > right
    if op in ("<",):
        return left < right
    if op in ("!=",):
        return left != right
    if op in ("==", "=", ""):
        # 只写数字时按前缀匹配：3.11 能匹配 3.11.2
        trimmed = _pad(version, len(target))
        return trimmed == right
    if op == "~=":
        # PEP 440：~=1.2 → >=1.2,<2.0；~=1.2.3 → >=1.2.3,<1.3.0
        prefix = right[:-1] if len(right) > 1 else right
        upper = (*prefix[:-1], prefix[-1] + 1)
        return left >= right and left < _pad(upper, len(left))
    if op == "^":
        # npm 语义：^1.5 → <2.0；^0.5 → <0.6（递增第一个非零段）
        position = next((index for index, part in enumerate(right) if part != 0), 0)
        upper = (*right[:position], right[position] + 1)
        return left >= right and left < _pad(upper, len(left))
    raise ValueError(f"不支持的运算符：{op!r}")


def _pad(version: tuple[int, ...], length: int) -> tuple[int, ...]:
    if len(version) >= length:
        return version[:length]
    return (*version, *([0] * (length - len(version))))
