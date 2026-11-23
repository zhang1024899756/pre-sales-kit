"""读取兼容性矩阵 json/yaml。

格式刻意宽松：顶层除了保留字段，其余键都当维度，value 可以是列表，
也可以是 {label, items}。这样加一个维度不用改代码。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pre_sales_kit.compat.models import (
    LEVEL_LABELS,
    Combination,
    CompatItem,
    CompatMatrix,
    Dimension,
    normalize_level,
)
from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.core.yamlfile import as_mapping, as_sequence, load_yaml, reject_unknown

_RESERVED = {"version", "product", "updated", "notes", "combinations", "labels", "description"}

_DEFAULT_LABELS = {
    "os": "操作系统",
    "db": "数据库",
    "database": "数据库",
    "browser": "浏览器",
    "cpu": "CPU",
    "middleware": "中间件",
    "jdk": "JDK",
    "java": "JDK",
    "cloud": "云平台",
    "chip": "芯片",
}

_ITEM_KEYS = {"name", "status", "note", "since"}


def load_matrix(path: Path | str) -> CompatMatrix:
    data, source = load_yaml(path)
    product = data.get("product")
    if not isinstance(product, str) or not product.strip():
        raise SpecError(message="缺少 product（产品或版本名），例如 product: 我们产品 v3.2", path=source)

    labels_raw = data.get("labels") or {}
    if not isinstance(labels_raw, dict):
        raise SpecError(message="labels 应该是「维度键 → 显示名」的映射", path=source)
    labels = {str(key): str(value) for key, value in labels_raw.items()}

    dimensions: list[Dimension] = []
    for key, raw in data.items():
        if key in _RESERVED:
            continue
        label = labels.get(key) or _DEFAULT_LABELS.get(key, str(key).upper())
        items_raw: Any
        if isinstance(raw, list):
            items_raw = raw
        elif isinstance(raw, dict):
            reject_unknown(raw, {"label", "items"}, where=f"维度「{key}」", path=source)
            label = str(raw.get("label") or label)
            items_raw = raw.get("items")
        else:
            raise SpecError(
                message=(
                    f"顶层字段 {key!r} 既不是维度（列表 / {{items: [...]}}），"
                    "也不是保留字段；检查拼写或改成维度格式"
                ),
                path=source,
            )
        items = _parse_items(as_sequence(items_raw, where=f"维度「{key}」", path=source), key, source)
        if not items:
            raise SpecError(message=f"维度「{key}」没有任何条目", path=source)
        duplicates = _duplicates([item.name for item in items])
        if duplicates:
            raise SpecError(message=f"维度「{key}」有重复条目：{'、'.join(duplicates)}", path=source)
        dimensions.append(Dimension(key=str(key), label=label, items=tuple(items)))

    if not dimensions:
        raise SpecError(
            message="没有任何维度：至少写一个，例如 os: [{name: Windows Server 2019, status: full}]",
            path=source,
        )

    combinations = _parse_combinations(data.get("combinations"), dimensions, source)
    notes = data.get("notes")
    updated = data.get("updated")
    return CompatMatrix(
        product=product.strip(),
        source=source,
        dimensions=tuple(dimensions),
        updated=str(updated) if updated is not None else None,
        notes=str(notes) if notes else None,
        combinations=tuple(combinations),
    )


def _parse_items(raw: list[Any], key: str, source: str) -> list[CompatItem]:
    items: list[CompatItem] = []
    for index, entry in enumerate(raw, start=1):
        where = f"维度「{key}」第 {index} 项"
        mapping = as_mapping(entry, where=where, path=source)
        reject_unknown(mapping, _ITEM_KEYS, where=where, path=source)
        name = mapping.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SpecError(message=f"{where} 缺少 name", path=source)
        raw_level = mapping.get("status")
        if raw_level is None:
            raise SpecError(
                message=f"{where}（{name}）缺少 status",
                path=source,
                hint=f"可用：{' / '.join(LEVEL_LABELS)}（也接受「支持 / 部分支持 / 不支持」）",
            )
        level = normalize_level(raw_level)
        if level == "unknown" and str(raw_level).strip().lower() not in {"unknown", "tbd", "待确认", "未知", "未验证"}:
            raise SpecError(
                message=f"{where}（{name}）的 status {raw_level!r} 不认识",
                hint=f"可用：{' / '.join(LEVEL_LABELS)}（也接受「支持 / 部分支持 / 不支持」）",
                path=source,
            )
        items.append(
            CompatItem(
                name=name.strip(),
                level=level,
                note=str(mapping["note"]).strip() if mapping.get("note") else None,
                since=str(mapping["since"]) if mapping.get("since") else None,
            )
        )
    return items


def _parse_combinations(
    raw: Any, dimensions: list[Dimension], source: str
) -> list[Combination]:
    if raw is None:
        return []
    combinations: list[Combination] = []
    for index, entry in enumerate(as_sequence(raw, where="combinations", path=source), start=1):
        where = f"combinations 第 {index} 项"
        mapping = as_mapping(entry, where=where, path=source)
        values = {key: str(value) for key, value in mapping.items() if key not in {"status", "note"}}
        unknown = [key for key in values if key not in {dim.key for dim in dimensions}]
        if unknown:
            raise SpecError(
                message=f"{where} 用了不存在的维度：{'、'.join(unknown)}",
                path=source,
                hint=f"已有维度：{'、'.join(dim.key for dim in dimensions)}",
            )
        if "status" not in mapping:
            raise SpecError(message=f"{where} 缺少 status", path=source)
        combinations.append(
            Combination(
                values=values,
                level=normalize_level(mapping["status"]),
                note=str(mapping["note"]).strip() if mapping.get("note") else None,
            )
        )
    return combinations


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    return duplicates
