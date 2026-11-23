"""读取并校验 doctor 的 YAML 声明。

校验从严：未知字段直接报错。理由很简单——YAML 里拼错一个键，
检查会被静默跳过，而你以为它跑过了，这在客户现场是灾难。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.core.text import parse_duration
from pre_sales_kit.core.yamlfile import (
    as_mapping,
    as_sequence,
    load_yaml,
    reject_unknown,
    require_str,
)
from pre_sales_kit.doctor.models import (
    FIX_ACTIONS,
    FIX_RISKS,
    CheckSpec,
    DoctorSpec,
    FixSpec,
)
from pre_sales_kit.doctor.registry import get as get_check_type
from pre_sales_kit.doctor.registry import load_builtin_checks

_TOP_KEYS = {"version", "title", "name", "description", "checks", "defaults"}
_CHECK_KEYS = {
    "id",
    "name",
    "type",
    "params",
    "fix",
    "tags",
    "timeout",
    "enabled",
    "description",
}
_FIX_KEYS = {"action", "command", "wait", "risk", "note"}
_DEFAULT_KEYS = {"timeout", "fix_wait"}


def load_spec(path: Path | str) -> DoctorSpec:
    load_builtin_checks()
    data, source = load_yaml(path)
    reject_unknown(data, _TOP_KEYS, where="声明文件顶层", path=source)

    version = data.get("version", 1)
    if version != 1:
        raise SpecError(message=f"不支持的声明版本 {version}（当前支持 1）", path=source)

    defaults = as_mapping(data.get("defaults") or {}, where="defaults", path=source)
    reject_unknown(defaults, _DEFAULT_KEYS, where="defaults", path=source)
    default_timeout = defaults.get("timeout")
    default_fix_wait = defaults.get("fix_wait")

    raw_checks = as_sequence(data.get("checks"), where="checks", path=source)
    if not raw_checks:
        raise SpecError(message="checks 是空的：至少写一条检查", path=source)

    checks: list[CheckSpec] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(raw_checks, start=1):
        where = f"checks 第 {index} 项"
        mapping = as_mapping(raw, where=where, path=source)
        reject_unknown(mapping, _CHECK_KEYS, where=where, path=source)
        if mapping.get("enabled", True) is False:
            continue
        name = require_str(mapping, "name", where=where, path=source)
        type_name = mapping.get("type")
        if not isinstance(type_name, str):
            raise SpecError(
                message=f"{where}（{name}）缺少 type，或 type 不是字符串",
                path=source,
                field="type",
            )
        get_check_type(type_name)  # 类型不存在时抛错，并列出可用类型

        check_id = str(mapping.get("id") or _slug(type_name, index)).strip()
        if check_id in seen:
            raise SpecError(
                message=f"{where} 的 id {check_id!r} 与第 {seen[check_id]} 项重复",
                path=source,
                field="id",
            )
        seen[check_id] = index

        params = as_mapping(mapping.get("params") or {}, where=f"{where} 的 params", path=source)

        tags_raw = as_sequence(mapping.get("tags"), where=f"{where} 的 tags", path=source)
        tags = tuple(str(tag) for tag in tags_raw)

        timeout_raw = mapping.get("timeout", default_timeout)
        timeout = float(parse_duration(timeout_raw)) if timeout_raw is not None else None

        fix = _parse_fix(mapping.get("fix"), where=where, path=source, default_wait=default_fix_wait)

        checks.append(
            CheckSpec(
                id=check_id,
                name=name,
                type=type_name,
                params=params,
                fix=fix,
                tags=tags,
                timeout=timeout,
                description=mapping.get("description"),
                source=source,
            )
        )

    if not checks:
        raise SpecError(message="所有检查都被 enabled: false 关掉了，没有可执行项", path=source)

    return DoctorSpec(
        title=str(data.get("title") or "演示环境自检"),
        name=data.get("name"),
        description=data.get("description"),
        source=source,
        checks=tuple(checks),
    )


def _parse_fix(
    raw: Any, *, where: str, path: str, default_wait: Any
) -> FixSpec | None:
    if raw is None:
        return None
    mapping = as_mapping(raw, where=f"{where} 的 fix", path=path)
    reject_unknown(mapping, _FIX_KEYS, where=f"{where} 的 fix", path=path)
    action = mapping.get("action")
    if action not in FIX_ACTIONS:
        raise SpecError(
            message=(
                f"{where} 的 fix.action 必须是 {' / '.join(FIX_ACTIONS)} 之一，实际是 {action!r}"
            ),
            path=path,
            field="fix.action",
        )
    command = mapping.get("command")
    if action in ("start_service", "run_command", "reseed") and not command:
        raise SpecError(
            message=f"{where} 的 fix.action 是 {action}，必须同时写 command",
            path=path,
            field="fix.command",
            hint="command 里可以用 {spec_dir}、{port} 等占位符，见 README",
        )
    risk = mapping.get("risk", "low")
    if risk not in FIX_RISKS:
        raise SpecError(
            message=f"{where} 的 fix.risk 必须是 low 或 high，实际是 {risk!r}",
            path=path,
            field="fix.risk",
        )
    wait_raw = mapping.get("wait", default_wait if default_wait is not None else 2.0)
    return FixSpec(
        action=action,
        command=str(command) if command else None,
        wait=float(parse_duration(wait_raw)),
        risk=risk,
        note=mapping.get("note"),
    )


def _slug(type_name: str, index: int) -> str:
    return f"{type_name}-{index}"

