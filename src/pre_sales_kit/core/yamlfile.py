"""读 YAML 声明：出错时说清文件、行号、字段，以及「应该写什么」。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pre_sales_kit.core.errors import KitError, SpecError


def load_yaml(path: Path | str) -> tuple[dict[str, Any], str]:
    """读一份 YAML，返回 (内容, 绝对路径字符串)。"""
    target = Path(path)
    if not target.exists():
        raise KitError(
            f"找不到声明文件：{target}",
            hint="确认路径；仓库里的 examples/ 目录下有可直接改的样板",
        )
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise KitError(f"声明文件不是 UTF-8 编码：{target}", hint=str(exc)) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise SpecError(
            message=f"YAML 语法错误：{getattr(exc, 'problem', exc)}",
            path=str(target),
            line=(mark.line + 1) if mark else None,
        ) from exc
    if data is None:
        raise SpecError(message="声明文件是空的", path=str(target))
    if not isinstance(data, dict):
        raise SpecError(message="声明文件顶层必须是键值映射", path=str(target))
    return data, str(target.resolve())


def as_mapping(value: Any, *, where: str, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecError(message=f"{where} 应该是键值映射，实际是 {type_name(value)}", path=path)
    return value


def as_sequence(value: Any, *, where: str, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(message=f"{where} 应该是列表，实际是 {type_name(value)}", path=path)
    return value


def require_str(mapping: dict[str, Any], key: str, *, where: str, path: str) -> str:
    if key not in mapping:
        raise SpecError(message=f"{where} 缺少必填字段 {key}", path=path, field=key)
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise SpecError(
            message=f"{where} 的 {key} 必须是非空字符串，实际是 {value!r}",
            path=path,
            field=key,
        )
    return value.strip()


def reject_unknown(
    mapping: dict[str, Any],
    allowed: set[str],
    *,
    where: str,
    path: str,
) -> None:
    """未知字段一律报错：YAML 拼错一个字母，检查会被静默跳过，这在现场是致命的。"""
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise SpecError(
            message=(
                f"{where} 出现未知字段 {', '.join(unknown)}；"
                f"可用字段：{', '.join(sorted(allowed))}"
            ),
            path=path,
        )


def type_name(value: Any) -> str:
    return {
        dict: "映射",
        list: "列表",
        str: "字符串",
        int: "整数",
        float: "小数",
        bool: "布尔值",
        type(None): "空值",
    }.get(type(value), type(value).__name__)
