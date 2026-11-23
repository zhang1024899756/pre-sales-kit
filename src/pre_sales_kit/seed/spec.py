"""读取并校验 seed 的 YAML 点位声明。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pre_sales_kit.core.errors import KitError, SpecError
from pre_sales_kit.core.text import parse_duration, parse_time_point
from pre_sales_kit.core.yamlfile import (
    as_mapping,
    as_sequence,
    load_yaml,
    reject_unknown,
    require_str,
)
from pre_sales_kit.seed.faults import FAULT_KINDS
from pre_sales_kit.seed.generators import GENERATORS
from pre_sales_kit.seed.models import FaultSpec, PointSpec, SeedSpec

_TOP_KEYS = {"version", "name", "description", "interval", "points"}
_POINT_KEYS = {
    "name",
    "model",
    "unit",
    "range",
    "baseline",
    "decimals",
    "params",
    "faults",
}
_FAULT_KEYS = {"kind", "from", "to", "magnitude", "note"}


def load_seed_spec(path: Path | str) -> SeedSpec:
    data, source = load_yaml(path)
    reject_unknown(data, _TOP_KEYS, where="声明文件顶层", path=source)

    version = data.get("version", 1)
    if version != 1:
        raise SpecError(message=f"不支持的声明版本 {version}（当前支持 1）", path=source)

    interval_raw = data.get("interval", "5s")
    try:
        interval = float(parse_duration(interval_raw))
    except KitError as exc:
        raise SpecError(message=f"interval 解析失败：{exc}", path=source, field="interval") from exc

    raw_points = as_sequence(data.get("points"), where="points", path=source)
    if not raw_points:
        raise SpecError(message="points 是空的：至少写一个点位", path=source)

    points: list[PointSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_points, start=1):
        where = f"points 第 {index} 项"
        mapping = as_mapping(raw, where=where, path=source)
        reject_unknown(mapping, _POINT_KEYS, where=where, path=source)
        name = require_str(mapping, "name", where=where, path=source)
        if name in seen:
            raise SpecError(message=f"点位名 {name!r} 重复", path=source, field="name")
        seen.add(name)

        model = require_str(mapping, "model", where=f"{where}（{name}）", path=source)
        if model not in GENERATORS:
            raise SpecError(
                message=(
                    f"点位「{name}」用了未知模型 {model!r}；"
                    f"可用：{'、'.join(sorted(GENERATORS))}"
                ),
                path=source,
                field="model",
            )

        low, high = _parse_range(mapping.get("range"), name=name, path=source)
        decimals = _parse_int(mapping.get("decimals", 2), default=2, name=name, path=source)
        if not 0 <= decimals <= 6:
            raise SpecError(
                message=f"点位「{name}」的 decimals 必须在 0~6 之间，实际是 {decimals}",
                path=source,
                field="decimals",
            )
        baseline = mapping.get("baseline")
        if baseline is not None and not isinstance(baseline, (int, float)):
            raise SpecError(message=f"点位「{name}」的 baseline 必须是数字", path=source)
        params = as_mapping(mapping.get("params") or {}, where=f"{where} 的 params", path=source)
        faults = _parse_faults(mapping.get("faults"), name=name, path=source)

        points.append(
            PointSpec(
                name=name,
                model=model,
                range_min=low,
                range_max=high,
                unit=str(mapping.get("unit") or ""),
                baseline=float(baseline) if baseline is not None else None,
                decimals=decimals,
                params=dict(params),
                faults=faults,
            )
        )

    return SeedSpec(
        name=str(data.get("name") or Path(source).stem),
        source=source,
        points=tuple(points),
        interval=interval,
        description=data.get("description"),
    )


def _parse_range(raw: Any, *, name: str, path: str) -> tuple[float, float]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise SpecError(
            message=f"点位「{name}」的 range 必须写成 [下限, 上限]",
            path=path,
            field="range",
            hint="例如 range: [20, 85]；量程是硬约束，生成的数据绝不越界",
        )
    try:
        low, high = float(raw[0]), float(raw[1])
    except (TypeError, ValueError) as exc:
        raise SpecError(
            message=f"点位「{name}」的 range 必须是两个数字，实际是 {raw!r}", path=path
        ) from exc
    if low >= high:
        raise SpecError(
            message=f"点位「{name}」的 range 下限必须小于上限，实际是 [{low}, {high}]", path=path
        )
    return low, high


def _parse_int(raw: Any, *, default: int, name: str, path: str) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SpecError(
            message=f"点位「{name}」的 decimals 必须是整数，实际是 {raw!r}", path=path
        )
    return raw


def _is_time_only(text: str) -> bool:
    import re

    return bool(re.fullmatch(r"\s*\d{1,2}:\d{2}(?::\d{2})?\s*", text))


def _parse_faults(raw: Any, *, name: str, path: str) -> tuple[FaultSpec, ...]:
    if raw is None:
        return ()
    items = as_sequence(raw, where=f"点位「{name}」的 faults", path=path)
    faults: list[FaultSpec] = []
    for index, item in enumerate(items, start=1):
        where = f"点位「{name}」的 faults 第 {index} 项"
        mapping = as_mapping(item, where=where, path=path)
        reject_unknown(mapping, _FAULT_KEYS, where=where, path=path)
        kind = mapping.get("kind")
        if kind not in FAULT_KINDS:
            raise SpecError(
                message=f"{where} 的 kind 必须是 {' / '.join(FAULT_KINDS)} 之一，实际是 {kind!r}",
                path=path,
            )
        from_raw = str(mapping.get("from"))
        to_raw = str(mapping.get("to"))
        try:
            start = parse_time_point(from_raw)
            end = parse_time_point(to_raw)
        except KitError as exc:
            raise SpecError(message=f"{where}：{exc}", path=path) from exc
        # 只写了时分 → 跟随数据集日期；写了完整日期 → 就用那一天
        time_of_day = _is_time_only(from_raw) and _is_time_only(to_raw)
        if end <= start:
            from datetime import timedelta

            end += timedelta(days=1)
        magnitude = mapping.get("magnitude", 0.2)
        if isinstance(magnitude, bool) or not isinstance(magnitude, (int, float)):
            raise SpecError(message=f"{where} 的 magnitude 必须是数字（相对量程的比例）", path=path)
        if not 0 < float(magnitude) <= 3:
            raise SpecError(
                message=f"{where} 的 magnitude 必须在 (0, 3] 之间，实际是 {magnitude}", path=path
            )
        faults.append(
            FaultSpec(
                kind=str(kind),
                start=start,
                end=end,
                magnitude=float(magnitude),
                note=mapping.get("note"),
                time_of_day=time_of_day,
            )
        )
    return tuple(faults)
