"""compat 的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 支持程度 → 展示词。顺序即严重程度。
LEVELS = ("full", "partial", "unsupported", "unknown")

LEVEL_LABELS = {
    "full": "完全支持",
    "partial": "部分支持",
    "unsupported": "不支持",
    "unknown": "待确认",
}

_ALIASES = {
    "full": "full",
    "supported": "full",
    "yes": "full",
    "true": "full",
    "支持": "full",
    "完全支持": "full",
    "已验证": "full",
    "partial": "partial",
    "limited": "partial",
    "部分支持": "partial",
    "有限支持": "partial",
    "有条件支持": "partial",
    "unsupported": "unsupported",
    "no": "unsupported",
    "false": "unsupported",
    "不支持": "unsupported",
    "不兼容": "unsupported",
    "unknown": "unknown",
    "tbd": "unknown",
    "待确认": "unknown",
    "未知": "unknown",
    "未验证": "unknown",
}


def normalize_level(value: object) -> str:
    return _ALIASES.get(str(value).strip().lower(), "unknown")


@dataclass(frozen=True)
class CompatItem:
    name: str
    level: str = "unknown"
    note: str | None = None
    since: str | None = None

    @property
    def label(self) -> str:
        return LEVEL_LABELS.get(self.level, self.level)


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    items: tuple[CompatItem, ...]

    def find(self, query: str) -> tuple[CompatItem | None, str]:
        """在维度里找条目；返回 (条目, 匹配方式)。模糊匹配会说明是怎么匹配上的。"""
        needle = query.strip().casefold()
        if not needle:
            return None, "empty"
        for item in self.items:
            if item.name.casefold() == needle:
                return item, "exact"
        # 「Windows Server 2016」vs「Windows Server 2016 标准版」
        contains = [item for item in self.items if needle in item.name.casefold()]
        if len(contains) == 1:
            return contains[0], "contains"
        starts = [item for item in self.items if item.name.casefold().startswith(needle)]
        if len(starts) == 1:
            return starts[0], "prefix"
        import difflib

        names = [item.name for item in self.items]
        close = difflib.get_close_matches(query, names, n=1, cutoff=0.72)
        if close:
            for item in self.items:
                if item.name == close[0]:
                    return item, "fuzzy"
        return None, "none"

    def suggest(self, limit: int = 3) -> list[str]:
        return [item.name for item in self.items[:limit]]


@dataclass(frozen=True)
class Combination:
    """显式声明的组合结论（比单维度推断更可信）。"""

    values: dict[str, str]
    level: str
    note: str | None = None


@dataclass(frozen=True)
class CompatMatrix:
    product: str
    source: str
    dimensions: tuple[Dimension, ...]
    updated: str | None = None
    notes: str | None = None
    combinations: tuple[Combination, ...] = ()

    def dimension(self, key: str) -> Dimension | None:
        for dimension in self.dimensions:
            if dimension.key == key:
                return dimension
        return None

    @property
    def dimension_keys(self) -> list[str]:
        return [dimension.key for dimension in self.dimensions]


@dataclass
class DimensionVerdict:
    key: str
    label: str
    query: str
    item: CompatItem | None
    matched_by: str = "exact"
    reason: str | None = None


@dataclass
class Verdict:
    level: str
    headline: str
    suggestion: str
    dimensions: list[DimensionVerdict] = field(default_factory=list)
    combination_note: str | None = None

    @property
    def exit_code(self) -> int:
        return {"full": 0, "unsupported": 1, "partial": 3, "unknown": 4}.get(self.level, 2)

    @property
    def label(self) -> str:
        return LEVEL_LABELS.get(self.level, self.level)
