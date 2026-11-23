"""CLI 端到端：退出码、JSON、落盘。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from pre_sales_kit.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "pre-sales-kit" in result.output


def test_help_lists_three_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("doctor", "seed", "compat"):
        assert name in result.output


def test_doctor_list_types() -> None:
    result = runner.invoke(app, ["doctor", "--list-types"])
    assert result.exit_code == 0
    assert "port" in result.output


def test_doctor_json_failure_exit_code(tmp_path) -> None:
    spec = tmp_path / "doctor.yaml"
    free_socket = __import__("socket").socket()
    free_socket.bind(("127.0.0.1", 0))
    port = free_socket.getsockname()[1]
    free_socket.close()
    spec.write_text(
        f"""
checks:
  - name: 端口
    type: port
    params: {{port: {port}}}
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor", str(spec), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["checks"][0]["status"] == "fail"
    assert payload["counts"]["fail"] == 1


def test_doctor_missing_spec(tmp_path) -> None:
    result = runner.invoke(app, ["doctor", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert "找不到" in result.output


def test_seed_list_models() -> None:
    result = runner.invoke(app, ["seed", "--list-models"])
    assert result.exit_code == 0
    for model in ("thermal", "load_step", "random_walk", "triangle", "counter"):
        assert model in result.output


def test_seed_writes_jsonl(tmp_path) -> None:
    model = tmp_path / "points.yaml"
    model.write_text(
        """
version: 1
interval: 60s
points:
  - name: 温度
    unit: ℃
    model: thermal
    range: [20, 85]
    decimals: 1
""",
        encoding="utf-8",
    )
    out = tmp_path / "demo.jsonl"
    result = runner.invoke(
        app,
        [
            "seed",
            "-m",
            str(model),
            "-o",
            str(out),
            "--start",
            "-1h",
            "--end",
            "now",
            "--interval",
            "60s",
            "--no-color",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 61
    assert "温度" in lines[0]


def test_seed_unknown_fault_point(tmp_path) -> None:
    model = tmp_path / "points.yaml"
    model.write_text(
        """
version: 1
points:
  - name: 温度
    model: thermal
    range: [20, 85]
""",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["seed", "-m", str(model), "-o", str(tmp_path / "x.db"), "--inject-fault", "10:00-11:00", "--fault-point", "不存在"],
    )
    assert result.exit_code == 2
    assert "不存在" in result.output


_MATRIX = """
product: 测试平台
os:
  - {name: Windows Server 2019, status: full}
  - {name: Windows Server 2016, status: partial, note: 需补丁}
db:
  - {name: Oracle 11g, status: unsupported}
  - {name: Oracle 19c, status: full}
"""


def test_compat_check_exit_codes(tmp_path) -> None:
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(_MATRIX, encoding="utf-8")

    ok = runner.invoke(
        app, ["compat", "check", "-m", str(matrix), "--os", "Windows Server 2019", "--db", "Oracle 19c"]
    )
    assert ok.exit_code == 0

    bad = runner.invoke(
        app, ["compat", "check", "-m", str(matrix), "--os", "Windows Server 2019", "--db", "Oracle 11g"]
    )
    assert bad.exit_code == 1
    assert "不应承诺" in bad.output

    partial = runner.invoke(
        app, ["compat", "check", "-m", str(matrix), "--os", "Windows Server 2016", "--db", "Oracle 19c"]
    )
    assert partial.exit_code == 3


def test_compat_check_json(tmp_path) -> None:
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(_MATRIX, encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "compat",
            "check",
            "-m",
            str(matrix),
            "--os",
            "Windows Server 2019",
            "--db",
            "Oracle 11g",
            "--json",
        ],
    )
    payload = json.loads(result.output)
    assert payload["level"] == "unsupported"
    assert result.exit_code == 1


def test_compat_matrix_exports(tmp_path) -> None:
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(_MATRIX, encoding="utf-8")
    html = tmp_path / "m.html"
    md = tmp_path / "m.md"
    result = runner.invoke(
        app,
        ["compat", "matrix", "-m", str(matrix), "--html", str(html), "--md", str(md), "--brand", "ACME"],
    )
    assert result.exit_code == 0
    assert "测试平台" in html.read_text(encoding="utf-8")
    assert "| 环境 | 支持情况 | 说明 |" in md.read_text(encoding="utf-8")


def test_compat_missing_matrix(tmp_path) -> None:
    result = runner.invoke(app, ["compat", "matrix", "-m", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
