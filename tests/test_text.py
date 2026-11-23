"""单位、时间、路径的小工具。现场人写的东西程序要都能认。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pre_sales_kit.core.errors import KitError
from pre_sales_kit.core.text import (
    display_source,
    format_ago,
    format_duration,
    human_size,
    parse_count,
    parse_duration,
    parse_resolution,
    parse_size,
    parse_time_point,
    parse_timestamp,
    parse_window,
)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30s", 30), ("5m", 300), ("2h", 7200), ("1.5d", 129600), ("500ms", 0.5), (90, 90), ("-24h", -86400)],
)
def test_parse_duration(text, seconds) -> None:
    assert parse_duration(text) == pytest.approx(seconds)


def test_parse_duration_rejects_garbage() -> None:
    with pytest.raises(KitError):
        parse_duration("两个钟头")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.85, "850ms"), (1.2, "1.2s"), (125, "2m05s"), (3780, "1h03m"), (None, "—")],
)
def test_format_duration(seconds, expected) -> None:
    assert format_duration(seconds) == expected


def test_format_ago() -> None:
    assert format_ago(12) == "12 秒前"
    assert format_ago(600) == "10 分钟前"
    assert format_ago(7200) == "2.0 小时前"


@pytest.mark.parametrize(("text", "size"), [("512MB", 512 * 1024**2), ("2GB", 2 * 1024**3), (1024, 1024)])
def test_parse_size(text, size) -> None:
    assert parse_size(text) == size


def test_human_size() -> None:
    assert human_size(1024**3) == "1.0GB"
    assert human_size(0) == "0B"


def test_parse_resolution() -> None:
    assert parse_resolution("1024x768") == (1024, 768)
    assert parse_resolution("1920 × 1080") == (1920, 1080)
    with pytest.raises(KitError):
        parse_resolution("全高清")


def test_parse_count() -> None:
    assert parse_count("1_000") == 1000
    assert parse_count("2.5k") == 2500
    assert parse_count(42) == 42


def test_parse_timestamp_variants() -> None:
    iso = "2026-11-23T10:00:00+08:00"
    assert parse_timestamp(iso) is not None
    epoch = parse_timestamp(1763856000)
    assert epoch is not None
    millis = parse_timestamp(1763856000000)
    assert millis is not None and abs((millis - epoch).total_seconds()) < 1
    assert parse_timestamp("2026-11-23 10:00:00") is not None
    assert parse_timestamp("不是时间") is None
    assert parse_timestamp(None) is None


def test_parse_time_point_offsets() -> None:
    now = datetime.now().astimezone()
    assert parse_time_point("now", now=now) == now
    assert parse_time_point("-24h", now=now) == now - timedelta(hours=24)
    assert parse_time_point("+30m", now=now) == now + timedelta(minutes=30)
    today_1420 = parse_time_point("14:20", now=now)
    assert (today_1420.hour, today_1420.minute) == (14, 20)


def test_parse_window() -> None:
    now = datetime(2026, 11, 23, 9, 0, tzinfo=datetime.now().astimezone().tzinfo)
    start, end = parse_window("14:20-14:35", now=now)
    assert (end - start) == timedelta(minutes=15)
    late_start, late_end = parse_window("23:50-00:10", now=now)
    assert late_end > late_start


def test_display_source_relative(tmp_path, monkeypatch) -> None:
    target = tmp_path / "spec.yaml"
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert display_source(str(target)) == "spec.yaml"
    outside = tmp_path.parent / "other.yaml"
    assert display_source(str(outside)) == str(outside)
