"""三种输出：SQLite / CSV / JSONL。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from pre_sales_kit.seed import sinks
from pre_sales_kit.seed.models import Dataset, PointSeries, PointSpec


@pytest.fixture
def dataset() -> Dataset:
    end = datetime(2026, 11, 23, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)
    times = [end - timedelta(minutes=2), end - timedelta(minutes=1), end]
    specs = [
        PointSpec("温度", "thermal", 20, 85, unit="℃", decimals=1),
        PointSpec("产量", "counter", 0, 100, unit="件", decimals=0),
    ]
    series = [
        PointSeries(
            spec=specs[0], times=times, values=[45.0, 45.5, 46.0]
        ),
        PointSeries(spec=specs[1], times=times, values=[10.0, 12.0, 14.0]),
    ]
    return Dataset(
        series=series,
        times=times,
        interval=60,
        seed=42,
        source="points.yaml",
        started_at=times[0],
        finished_at=end,
    )


def test_sqlite(dataset, tmp_path) -> None:
    result = sinks.write_sqlite(dataset, tmp_path / "demo.db")
    assert result.rows == 6
    connection = sqlite3.connect(tmp_path / "demo.db")
    try:
        total = connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        points = {row[0] for row in connection.execute("SELECT DISTINCT point FROM measurements")}
        meta = dict(connection.execute("SELECT key, value FROM seed_meta").fetchall())
    finally:
        connection.close()
    assert total == 6
    assert points == {"温度", "产量"}
    assert meta["seed"] == "42"
    assert meta["rows"] == "6"


def test_sqlite_replaces_existing_file(dataset, tmp_path) -> None:
    target = tmp_path / "demo.db"
    target.write_text("not a database", encoding="utf-8")
    result = sinks.write_sqlite(dataset, target)
    assert result.rows == 6
    assert "覆盖" in result.detail


def test_csv_is_excel_friendly(dataset, tmp_path) -> None:
    target = tmp_path / "demo.csv"
    result = sinks.write_csv(dataset, target)
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM：Excel 双击不乱码
    text = raw.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].startswith("ts,温度(℃),产量(件)")
    assert len(lines) == 4
    assert result.rows == 3


def test_jsonl(dataset, tmp_path) -> None:
    target = tmp_path / "demo.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        result = sinks.write_jsonl(dataset, handle)
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert result.rows == 3
    first = json.loads(lines[0])
    assert first["values"]["温度"] == 45.0
    assert "T" in first["ts"]


def test_api_requires_http(dataset) -> None:
    from pre_sales_kit.core.errors import KitError

    with pytest.raises(KitError):
        sinks.post_api(dataset, "ftp://example.com")


def test_api_dry_run_does_not_send(dataset, capsys) -> None:
    result = sinks.post_api(dataset, "http://127.0.0.1:9/ingest", batch_size=2, dry_run=True)
    assert result.rows == 6
    assert "预演" in result.detail
    assert "records" in capsys.readouterr().out
