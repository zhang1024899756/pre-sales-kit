"""统一的健康状态词表。

终端、HTML 报告、JSON、退出码全部以这里为准，避免出现「这里有绿色那里写 OK」的不一致。
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """一项检查（或一次修复）的结果。"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def glyph(self) -> str:
        """终端里用的单字符标记。不用 emoji：Windows 终端与 git diff 里都会变形。"""
        return _GLYPHS[self]

    @property
    def rank(self) -> int:
        """用于聚合：越大越严重。SKIP 不参与聚合。"""
        return _RANKS[self]

    @property
    def is_problem(self) -> bool:
        return self in (Status.WARN, Status.FAIL)

    def __str__(self) -> str:  # pragma: no cover - 调试便利
        return self.value


_LABELS = {
    Status.PASS: "通过",
    Status.WARN: "警告",
    Status.FAIL: "失败",
    Status.SKIP: "跳过",
}

_GLYPHS = {
    Status.PASS: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.SKIP: "–",
}

_RANKS = {
    Status.PASS: 0,
    Status.WARN: 1,
    Status.FAIL: 2,
    Status.SKIP: -1,
}


def worst(statuses: list[Status]) -> Status:
    """聚合多个状态：取最严重的那个；全跳过时返回 SKIP。"""
    ranked = [s for s in statuses if s is not Status.SKIP]
    if not ranked:
        return Status.SKIP
    return max(ranked, key=lambda s: s.rank)


def from_any(value: object) -> Status:
    """把 YAML 里人写的各种状态词归一化。"""
    text = str(value).strip().lower()
    return _ALIASES.get(text, Status.FAIL)


_ALIASES: dict[str, Status] = {
    "pass": Status.PASS,
    "ok": Status.PASS,
    "true": Status.PASS,
    "success": Status.PASS,
    "green": Status.PASS,
    "通过": Status.PASS,
    "正常": Status.PASS,
    "warn": Status.WARN,
    "warning": Status.WARN,
    "yellow": Status.WARN,
    "警告": Status.WARN,
    "注意": Status.WARN,
    "fail": Status.FAIL,
    "false": Status.FAIL,
    "error": Status.FAIL,
    "red": Status.FAIL,
    "失败": Status.FAIL,
    "异常": Status.FAIL,
    "skip": Status.SKIP,
    "skipped": Status.SKIP,
    "n/a": Status.SKIP,
    "跳过": Status.SKIP,
}
