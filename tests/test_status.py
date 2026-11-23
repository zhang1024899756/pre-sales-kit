from __future__ import annotations

from pre_sales_kit.core.status import Status, from_any, worst


def test_worst_picks_most_severe() -> None:
    assert worst([Status.PASS, Status.WARN, Status.FAIL]) is Status.FAIL
    assert worst([Status.PASS, Status.SKIP]) is Status.PASS
    assert worst([Status.SKIP]) is Status.SKIP


def test_aliases() -> None:
    assert from_any("OK") is Status.PASS
    assert from_any("警告") is Status.WARN
    assert from_any("不支持") is Status.FAIL
    assert from_any("n/a") is Status.SKIP


def test_glyphs_are_plain_ascii_friendly() -> None:
    for status in Status:
        assert len(status.glyph) == 1
        assert status.label


def test_status_is_string_enum() -> None:
    assert Status.PASS == "pass"
    assert str(Status.WARN.value) == "warn"
