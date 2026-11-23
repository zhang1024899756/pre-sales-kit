"""七类检查的行为：全部用真的端口、真的 HTTP 服务、真的 SQLite。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tests.conftest import make_spec

from pre_sales_kit.core.status import Status
from pre_sales_kit.doctor.checks.data import run_data
from pre_sales_kit.doctor.checks.display import run_display
from pre_sales_kit.doctor.checks.env import run_env
from pre_sales_kit.doctor.checks.network import run_network
from pre_sales_kit.doctor.checks.ports import run_port
from pre_sales_kit.doctor.checks.services import run_service
from pre_sales_kit.doctor.checks.version import run_version, satisfies


def _status(items) -> Status:
    from pre_sales_kit.core.status import worst

    return worst([item.status for item in items])


# ---------------- 端口 ----------------


def test_port_listening(listening_port, ctx) -> None:
    items = run_port(make_spec("port", {"port": listening_port}), ctx)
    assert items[0].status is Status.PASS
    assert "正在监听" in items[0].detail


def test_port_not_listening(free_port, ctx) -> None:
    items = run_port(make_spec("port", {"port": free_port}), ctx)
    assert items[0].status is Status.FAIL
    assert items[0].hint


def test_port_expect_free_but_occupied(listening_port, ctx) -> None:
    items = run_port(make_spec("port", {"port": listening_port, "expect": "free"}), ctx)
    assert items[0].status is Status.FAIL


def test_port_expect_free_ok(free_port, ctx) -> None:
    items = run_port(make_spec("port", {"port": free_port, "expect": "free"}), ctx)
    assert items[0].status is Status.PASS


def test_port_invalid_params(ctx) -> None:
    from pre_sales_kit.core.errors import SpecError

    with pytest.raises(SpecError):
        run_port(make_spec("port", {"port": 99999}), ctx)
    with pytest.raises(SpecError):
        run_port(make_spec("port", {"port": 80, "prot": 1}), ctx)


# ---------------- 服务 ----------------


def test_service_ok(http_endpoint, ctx) -> None:
    items = run_service(make_spec("service", {"url": f"{http_endpoint}/health"}), ctx)
    assert _status(items) is Status.PASS
    assert "HTTP 200" in items[0].detail


def test_service_body_mismatch(http_endpoint, ctx) -> None:
    items = run_service(
        make_spec("service", {"url": f"{http_endpoint}/health", "body_contains": '"status": "down"'}), ctx
    )
    assert _status(items) is Status.FAIL
    assert "响应体缺少" in items[0].detail


def test_service_bad_status(http_endpoint, ctx) -> None:
    items = run_service(make_spec("service", {"url": f"{http_endpoint}/bad"}), ctx)
    assert _status(items) is Status.FAIL
    assert "503" in items[0].detail


def test_service_allows_redirect(http_endpoint, ctx) -> None:
    items = run_service(
        make_spec("service", {"url": f"{http_endpoint}/redirect", "allow_redirects": True}), ctx
    )
    assert _status(items) is Status.PASS


def test_service_rejects_redirect_when_disabled(http_endpoint, ctx) -> None:
    items = run_service(
        make_spec("service", {"url": f"{http_endpoint}/redirect", "allow_redirects": False}), ctx
    )
    assert _status(items) is Status.FAIL
    assert "302" in items[0].detail


def test_service_unreachable(free_port, ctx) -> None:
    items = run_service(make_spec("service", {"url": f"http://127.0.0.1:{free_port}/health"}), ctx)
    assert _status(items) is Status.FAIL
    assert "连不上" in items[0].detail


# ---------------- 数据 ----------------


def _make_db(path: Path, *, rows: int = 10, latest: datetime | None = None) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE measurements (ts TEXT, point TEXT, value REAL)")
    moment = latest or datetime.now().astimezone()
    connection.executemany(
        "INSERT INTO measurements VALUES (?, ?, ?)",
        [
            ((moment - timedelta(minutes=index)).isoformat(timespec="seconds"), "主轴温度", 45.0)
            for index in range(rows)
        ],
    )
    connection.commit()
    connection.close()


def test_data_ok(tmp_path, ctx) -> None:
    _make_db(tmp_path / "demo.db")
    items = run_data(
        make_spec("data", {"path": "demo.db", "table": "measurements", "min_rows": 5}), ctx
    )
    assert _status(items) is Status.PASS
    assert "10 行" in items[0].detail


def test_data_missing_file(ctx) -> None:
    items = run_data(make_spec("data", {"path": "nope.db", "table": "measurements"}), ctx)
    assert _status(items) is Status.FAIL
    assert "不存在" in items[0].detail


def test_data_too_few_rows(tmp_path, ctx) -> None:
    _make_db(tmp_path / "demo.db", rows=3)
    items = run_data(
        make_spec("data", {"path": "demo.db", "table": "measurements", "min_rows": 100}), ctx
    )
    assert _status(items) is Status.FAIL
    assert "只有 3 行" in items[0].detail


def test_data_stale(tmp_path, ctx) -> None:
    _make_db(tmp_path / "demo.db", latest=datetime.now().astimezone() - timedelta(days=3))
    items = run_data(
        make_spec(
            "data",
            {
                "path": "demo.db",
                "table": "measurements",
                "time_column": "ts",
                "latest_within": "1h",
            },
        ),
        ctx,
    )
    assert _status(items) is Status.FAIL
    assert "最新数据" in items[0].detail


def test_data_missing_table_suggests_name(tmp_path, ctx) -> None:
    _make_db(tmp_path / "demo.db")
    items = run_data(
        make_spec("data", {"path": "demo.db", "table": "measurement"}), ctx
    )
    assert _status(items) is Status.FAIL
    assert items[0].hint and "measurements" in items[0].hint


# ---------------- 环境 ----------------


def test_env_disk_ok(ctx) -> None:
    items = run_env(make_spec("env", {"disk_min_free": "1MB"}), ctx)
    assert _status(items) is Status.PASS


def test_env_disk_too_big_threshold(ctx) -> None:
    items = run_env(make_spec("env", {"disk_min_free": "1PB"}), ctx)
    assert _status(items) is Status.FAIL


def test_env_timezone_matches_local(ctx) -> None:
    offset = datetime.now().astimezone().utcoffset()
    assert offset is not None
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    items = run_env(make_spec("env", {"timezone": f"{sign}{hours:02d}:{rest:02d}"}), ctx)
    assert _status(items) is Status.PASS


def test_env_timezone_mismatch(ctx) -> None:
    offset = datetime.now().astimezone().utcoffset()
    assert offset is not None
    wrong = "+12:00" if offset.total_seconds() < 0 else "-11:00"
    items = run_env(make_spec("env", {"timezone": wrong}), ctx)
    assert _status(items) is Status.WARN


def test_env_requires_something(ctx) -> None:
    from pre_sales_kit.core.errors import SpecError

    with pytest.raises(SpecError):
        run_env(make_spec("env", {}), ctx)


def test_env_cert_missing(ctx) -> None:
    items = run_env(make_spec("env", {"cert_path": "nope.pem"}), ctx)
    assert _status(items) is Status.FAIL


# ---------------- 版本 ----------------


@pytest.mark.parametrize(
    ("version", "constraint", "expected"),
    [
        ((3, 11, 2), ">=3.11", True),
        ((3, 10, 0), ">=3.11", False),
        ((3, 11, 2), "3.11", True),
        ((3, 12, 0), "3.11", False),
        ((1, 2, 3), "~=1.2", True),
        ((1, 3, 0), "~=1.2", True),  # PEP 440：~=1.2 等价于 >=1.2,<2
        ((2, 0, 0), "~=1.2", False),
        ((1, 2, 3), "~=1.2.3", True),
        ((1, 3, 0), "~=1.2.3", False),
        ((2, 0, 0), "^1.5", False),
        ((1, 5, 9), "^1.5", True),
        ((3, 12, 1), ">=3.11,<3.13", True),
        ((3, 13, 0), ">=3.11,<3.13", False),
    ],
)
def test_satisfies(version, constraint, expected) -> None:
    assert satisfies(version, constraint) is expected


def test_version_python_tool(ctx) -> None:
    items = run_version(make_spec("version", {"tools": {"python": ">=3.11"}}), ctx)
    assert _status(items) is Status.PASS


def test_version_missing_tool(ctx) -> None:
    items = run_version(make_spec("version", {"tools": {"definitely-not-a-real-tool": ">=1"}}), ctx)
    assert _status(items) is Status.FAIL


def test_version_missing_package(ctx) -> None:
    items = run_version(make_spec("version", {"python_packages": {"no-such-pkg-xyz": ">=1"}}), ctx)
    assert _status(items) is Status.FAIL


# ---------------- 网络 ----------------


def test_network_reachable(listening_port, ctx) -> None:
    spec = make_spec(
        "network",
        {"targets": [{"name": "本机", "host": "127.0.0.1", "port": listening_port}]},
    )
    items = run_network(spec, ctx)
    assert _status(items) is Status.PASS


def test_network_unreachable_expected(free_port, ctx) -> None:
    spec = make_spec(
        "network",
        {
            "targets": [
                {"name": "外网", "host": "127.0.0.1", "port": free_port, "expect": "unreachable"}
            ]
        },
    )
    items = run_network(spec, ctx)
    assert _status(items) is Status.PASS


def test_network_invalid_target(ctx) -> None:
    from pre_sales_kit.core.errors import SpecError

    with pytest.raises(SpecError):
        run_network(make_spec("network", {"targets": [{"host": "127.0.0.1"}]}), ctx)


# ---------------- 显示 ----------------


def test_display_returns_resolution_and_browsers(ctx) -> None:
    items = run_display(make_spec("display", {"min_resolution": "640x480"}), ctx)
    assert len(items) == 2
    assert items[0].label == "分辨率"
    assert items[1].label == "浏览器"
