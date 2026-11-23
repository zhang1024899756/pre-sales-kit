"""数据预览 HTML：一张图看清每条曲线长什么样、有没有硬伤。

和 doctor 报告同一套排版语言（同一份 tokens），但这里的主角是曲线。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pre_sales_kit import __version__
from pre_sales_kit.core.text import display_source, format_duration
from pre_sales_kit.seed.faults import fault_label
from pre_sales_kit.seed.service import SeedRun, _jump_limit

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_SAMPLES = 480
_HEIGHT = 104
_WIDTH = 880


def write_preview(run: SeedRun, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_preview(run), encoding="utf-8")
    return target


def render_preview(run: SeedRun, *, generated_at: datetime | None = None) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template("seed_preview.html.j2").render(
        view=build_view(run, generated_at=generated_at)
    )


def build_view(run: SeedRun, *, generated_at: datetime | None = None) -> dict[str, Any]:
    dataset = run.dataset
    moment = generated_at or datetime.now().astimezone()
    cards = []
    for series, stat in zip(dataset.series, run.stats, strict=True):
        chart = sparkline(series.values, dataset.times)
        issues = stat.problems(jump_ratio=_jump_limit(stat.model))
        cards.append(
            {
                "name": series.spec.name,
                "unit": series.spec.unit,
                "model": series.spec.model,
                "count": f"{stat.count:,}",
                "minimum": _fmt(stat.minimum, series.spec.decimals),
                "maximum": _fmt(stat.maximum, series.spec.decimals),
                "range": f"{_fmt(series.spec.range_min, series.spec.decimals)} – "
                f"{_fmt(series.spec.range_max, series.spec.decimals)}",
                "max_step": _fmt(stat.max_step, max(series.spec.decimals, 2)),
                "chart": chart,
                "ok": not issues,
                "issues": issues,
                "faults": [
                    {
                        "label": fault_label(fault.kind),
                        "window": f"{fault.start.strftime('%H:%M')}–{fault.end.strftime('%H:%M')}",
                        "magnitude": f"{fault.magnitude * 100:+.0f}% 量程",
                        "left": chart["left"](fault.start),
                        "width": chart["width"](fault),
                    }
                    for fault in run.applied.get(series.spec.name, [])
                ],
            }
        )

    total_points = dataset.point_count
    failure_count = len(run.failed_points)
    return {
        "title": dataset.title or "演示数据预览",
        "source": display_source(dataset.source),
        "generated": moment,
        "version": __version__,
        "seed": dataset.seed,
        "interval": f"{dataset.interval:g}s",
        "points": cards,
        "point_count": total_points,
        "total_rows": f"{dataset.total_rows:,}",
        "samples": f"{len(dataset.times):,}",
        "duration": format_duration((dataset.times[-1] - dataset.times[0]).total_seconds()),
        "window_start": dataset.times[0].strftime("%Y-%m-%d %H:%M"),
        "window_end": dataset.times[-1].strftime("%Y-%m-%d %H:%M"),
        "passed": total_points - failure_count,
        "failed": failure_count,
    }


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def sparkline(values: list[float], times: list[datetime]) -> dict[str, Any]:
    """把一列数据画成「均值折线 + 最小值/最大值包络」。

    直接抽稀画折线是错的：24 小时压进 880px，每像素好几个采样点，
    连起来就是一堵锯齿墙，看着像坏数据。包络既保留真实幅度，也画得干净。
    """
    empty = {
        "path": "",
        "envelope": "",
        "low": 0.0,
        "high": 0.0,
        "left": lambda _m: 0.0,
        "width": lambda _f: 0.0,
    }
    if not values:
        return empty

    width, height = _WIDTH, _HEIGHT
    buckets = min(_SAMPLES // 2, len(values))
    total = len(values)
    lows: list[float] = []
    highs: list[float] = []
    means: list[float] = []
    xs: list[float] = []
    for index in range(buckets):
        begin = index * total // buckets
        end = max(begin + 1, (index + 1) * total // buckets)
        segment = values[begin:end]
        lows.append(min(segment))
        highs.append(max(segment))
        means.append(sum(segment) / len(segment))
        xs.append(width * index / max(1, buckets - 1))

    low, high = min(lows), max(highs)
    if high - low < 1e-9:
        low -= 1.0
        high += 1.0
    padding = (high - low) * 0.08
    low -= padding
    high += padding

    def y_of(value: float) -> float:
        return height - (value - low) / (high - low) * height

    upper = " L".join(f"{x:.1f},{y_of(value):.1f}" for x, value in zip(xs, highs, strict=True))
    lower = " L".join(
        f"{x:.1f},{y_of(value):.1f}"
        for x, value in reversed(list(zip(xs, lows, strict=True)))
    )
    envelope = f"M{upper} L{lower} Z"
    path = "M" + " L".join(
        f"{x:.1f},{y_of(value):.1f}" for x, value in zip(xs, means, strict=True)
    )

    span_seconds = max(1.0, (times[-1] - times[0]).total_seconds())
    start = times[0]

    def left(moment: datetime) -> float:
        ratio = (moment - start).total_seconds() / span_seconds
        return round(min(100.0, max(0.0, ratio * 100)), 2)

    def band_width(fault: Any) -> float:
        span = (fault.end - fault.start).total_seconds() / span_seconds
        return round(min(100.0, max(0.4, span * 100)), 2)

    return {
        "path": path,
        "envelope": envelope,
        "low": low,
        "high": high,
        "left": left,
        "width": band_width,
    }
