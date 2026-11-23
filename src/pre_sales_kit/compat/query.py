"""兼容性结论的计算：逐维度查 → 命中组合优先 → 汇总。

结论措辞刻意「像人说的」：它的用途是投标前提醒自己别把话说满。
"""

from __future__ import annotations

from pre_sales_kit.compat.models import (
    LEVEL_LABELS,
    CompatMatrix,
    DimensionVerdict,
    Verdict,
)

_HEADLINES = {
    "full": "可以承诺：这个组合已验证支持",
    "partial": "有条件支持：投标前必须把前提写进方案书",
    "unsupported": "不应承诺：这个组合不受支持",
    "unknown": "无法判断：有维度没有收录，先找研发确认",
}

_SUGGESTIONS = {
    "full": "可以直接应答「支持」。",
    "partial": "应答为「支持，但需满足下列前提」，并把说明写进偏离表。",
    "unsupported": "应答为「不支持」，同时给出替代方案（升级版本 / 改用受支持的组合）。",
    "unknown": "不要答「支持」；先确认后再答复客户，别在标书里写没验证过的组合。",
}

_SEVERITY = {"full": 0, "partial": 1, "unsupported": 2, "unknown": 3}


def evaluate(matrix: CompatMatrix, query: dict[str, str]) -> Verdict:
    verdicts: list[DimensionVerdict] = []
    for key, value in query.items():
        dimension = matrix.dimension(key)
        if dimension is None:
            verdicts.append(
                DimensionVerdict(
                    key=key,
                    label=key,
                    query=value,
                    item=None,
                    reason=f"矩阵里没有这个维度（已有：{'、'.join(matrix.dimension_keys)}）",
                )
            )
            continue
        item, matched_by = dimension.find(value)
        verdicts.append(
            DimensionVerdict(
                key=key,
                label=dimension.label,
                query=value,
                item=item,
                matched_by=matched_by,
                reason=None
                if item
                else f"矩阵里没有收录（例如：{' / '.join(dimension.suggest())}）",
            )
        )

    level, note = _resolve(matrix, verdicts)
    headline = _HEADLINES[level]
    suggestion = _SUGGESTIONS[level]
    if note:
        suggestion = f"{note}。{suggestion}"
    return Verdict(
        level=level,
        headline=headline,
        suggestion=suggestion,
        dimensions=verdicts,
        combination_note=note,
    )


def _resolve(matrix: CompatMatrix, verdicts: list[DimensionVerdict]) -> tuple[str, str | None]:
    if not verdicts:
        return "unknown", None

    # 1) 显式声明过的组合最可信
    if all(verdict.item is not None for verdict in verdicts):
        resolved = {verdict.key: verdict.item.name for verdict in verdicts if verdict.item}
        for combination in matrix.combinations:
            if _same(combination.values, resolved):
                return combination.level, combination.note

    # 2) 逐维度取最差
    level = "full"
    for verdict in verdicts:
        if verdict.item is None:
            return "unknown", None
        if _SEVERITY[verdict.item.level] > _SEVERITY[level]:
            level = verdict.item.level
    return level, None


def _same(left: dict[str, str], right: dict[str, str]) -> bool:
    if set(left) != set(right):
        return False
    return all(left[key].casefold() == right[key].casefold() for key in left)


def describe(verdict: Verdict) -> list[str]:
    """给人看的逐维度结论。"""
    lines: list[str] = []
    for item in verdict.dimensions:
        if item.item is None:
            lines.append(f"{item.label}: 未收录（{item.reason}）")
            continue
        label = LEVEL_LABELS.get(item.item.level, item.item.level)
        text = f"{item.label}: {item.item.name} — {label}"
        if item.item.note:
            text += f"（需注意：{item.item.note}）"
        if item.matched_by == "fuzzy":
            text += f"  [按「{item.query}」近似匹配]"
        lines.append(text)
    return lines
