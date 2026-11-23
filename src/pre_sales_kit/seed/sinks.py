"""三种输出：SQLite / CSV / 直接打 API。

客户现场的现实：给他一个 Excel 他能看，给他一个接口他能接，
给他一个 SQLite 他能自己查——所以三种都支持，不强迫。
"""

from __future__ import annotations

import csv
import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from pre_sales_kit.core.errors import KitError
from pre_sales_kit.seed.models import Dataset

_CHUNK = 5000


@dataclass
class WriteResult:
    format: str
    location: str
    rows: int
    bytes_written: int | None = None
    detail: str = ""


def timestamp_text(moment: datetime, *, style: str = "iso") -> str:
    if style == "human":
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    return moment.isoformat(timespec="seconds")


def write_sqlite(dataset: Dataset, path: Path | str) -> WriteResult:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    replaced = target.exists()
    if replaced:
        target.unlink()
    connection = sqlite3.connect(target)
    try:
        connection.executescript(
            """
            CREATE TABLE measurements (
                ts TEXT NOT NULL,
                point TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                PRIMARY KEY (ts, point)
            );
            CREATE INDEX idx_measurements_ts ON measurements (ts);
            CREATE INDEX idx_measurements_point ON measurements (point);
            CREATE TABLE seed_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        rows: list[tuple[str, str, float, str]] = []
        written = 0
        for index, moment in enumerate(dataset.times):
            stamp = timestamp_text(moment)
            for series in dataset.series:
                rows.append((stamp, series.spec.name, series.values[index], series.spec.unit))
            if len(rows) >= _CHUNK:
                connection.executemany("INSERT INTO measurements VALUES (?,?,?,?)", rows)
                written += len(rows)
                rows.clear()
        if rows:
            connection.executemany("INSERT INTO measurements VALUES (?,?,?,?)", rows)
            written += len(rows)
        meta = {
            "generated_at": dataset.finished_at.isoformat(timespec="seconds"),
            "source": dataset.source,
            "seed": str(dataset.seed),
            "interval": str(dataset.interval),
            "points": ",".join(series.spec.name for series in dataset.series),
            "rows": str(written),
        }
        connection.executemany(
            "INSERT INTO seed_meta VALUES (?, ?)", [(key, value) for key, value in meta.items()]
        )
        connection.commit()
    finally:
        connection.close()
    size = target.stat().st_size
    return WriteResult(
        format="sqlite",
        location=str(target),
        rows=written,
        bytes_written=size,
        detail="覆盖已有文件" if replaced else "新建",
    )


def write_csv(dataset: Dataset, path: Path | str) -> WriteResult:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    headers = ["ts"] + [
        f"{series.spec.name}({series.spec.unit})" if series.spec.unit else series.spec.name
        for series in dataset.series
    ]
    # utf-8-sig：Excel 双击打开不会把中文显示成乱码
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for index, moment in enumerate(dataset.times):
            writer.writerow(
                [timestamp_text(moment, style="human")]
                + [series.values[index] for series in dataset.series]
            )
    return WriteResult(
        format="csv",
        location=str(target),
        rows=len(dataset.times),
        bytes_written=target.stat().st_size,
        detail=f"{len(headers)} 列（宽表，Excel 友好）",
    )


def write_jsonl(dataset: Dataset, stream: IO[str]) -> WriteResult:
    written = 0
    for index, moment in enumerate(dataset.times):
        payload = {
            "ts": timestamp_text(moment),
            "values": {
                series.spec.name: series.values[index] for series in dataset.series
            },
        }
        stream.write(json.dumps(payload, ensure_ascii=False))
        stream.write("\n")
        written += 1
    return WriteResult(format="jsonl", location="stdout", rows=written, detail="每行一个时间点")


def post_api(
    dataset: Dataset,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    batch_size: int = 500,
    timeout: float = 10.0,
    retries: int = 2,
    dry_run: bool = False,
) -> WriteResult:
    """把数据按批 POST 到 /ingest 之类的接口。

    请求体：{"records": [{"ts","point","value","unit"}...], "meta": {"source": "pre-sales-kit"}}
    接口签名不一样时，用 --format jsonl 输出再由你自己的脚本转发。
    """
    if not url.lower().startswith(("http://", "https://")):
        raise KitError(f"--url 必须是 http(s) 地址，实际是 {url!r}")
    records: list[dict[str, Any]] = []
    for index, moment in enumerate(dataset.times):
        stamp = timestamp_text(moment)
        for series in dataset.series:
            records.append(
                {
                    "ts": stamp,
                    "point": series.spec.name,
                    "value": series.values[index],
                    "unit": series.spec.unit,
                }
            )

    batches = [records[start : start + batch_size] for start in range(0, len(records), batch_size)]
    sent = 0
    for number, batch in enumerate(batches, start=1):
        body = json.dumps(
            {"records": batch, "meta": {"source": "pre-sales-kit", "batch": number}},
            ensure_ascii=False,
        ).encode("utf-8")
        if dry_run:
            if number == 1:
                print(json.dumps(json.loads(body), ensure_ascii=False, indent=2)[:1200])
                print(f"... 共 {len(batches)} 批 / {len(records):,} 条，--dry-run 不实际发送")
            sent += len(batch)
            continue
        _post_with_retry(url, body, headers or {}, timeout, retries)
        sent += len(batch)
    verb = "预演" if dry_run else "发送"
    return WriteResult(
        format="api",
        location=url,
        rows=sent,
        detail=f"{verb} {len(batches)} 批（每批 {batch_size} 条）",
    )


def _post_with_retry(
    url: str, body: bytes, headers: dict[str, str], timeout: float, retries: int
) -> None:
    request_headers = {"Content-Type": "application/json; charset=utf-8", **headers}
    last_error = ""
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(0.5 * 2**attempt, 5.0))
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if 200 <= response.status < 300:
                    return
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code < 500:
                break
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
        except (TimeoutError, OSError) as exc:
            last_error = str(exc)
    raise KitError(f"推送失败：{url}（{last_error}）", hint="确认接口地址、鉴权头，或先用 --dry-run 看请求体")
