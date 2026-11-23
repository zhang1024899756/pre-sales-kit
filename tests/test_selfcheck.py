"""kit selfcheck：安装自检。

这些断言的意义：自检本身不能是个「永远绿」的装饰品。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pre_sales_kit import __version__, selfcheck
from pre_sales_kit.cli import app
from pre_sales_kit.core.status import Status

runner = CliRunner()


def test_python_check_passes_here() -> None:
    assert selfcheck.check_python().status is Status.PASS


def test_packaged_data_is_complete() -> None:
    statuses = [check.status for check in selfcheck.check_data_files()]
    assert statuses == [Status.PASS, Status.PASS]


def test_run_checks_covers_the_essentials() -> None:
    checks = selfcheck.run_checks(directory=Path.cwd())
    keys = {check.key for check in checks}
    assert {"python", "templates", "scaffold", "entry", "encoding", "write"} <= keys


def test_json_payload_round_trips() -> None:
    payload = selfcheck.to_dict(selfcheck.run_checks(directory=Path.cwd()))
    decoded = json.loads(json.dumps(payload, ensure_ascii=False))
    assert decoded["version"] == __version__
    assert decoded["checks"]


def test_exit_code_only_fails_on_failures() -> None:
    ok = [selfcheck.Check("a", "a", Status.PASS, "")]
    warned = [selfcheck.Check("a", "a", Status.WARN, ""), *ok]
    failed = [selfcheck.Check("a", "a", Status.FAIL, "坏"), *ok]
    assert selfcheck.exit_code(ok) == 0
    assert selfcheck.exit_code(warned) == 0
    assert selfcheck.exit_code(failed) == 1


def test_cli_json_output(tmp_path: Path) -> None:
    result = runner.invoke(app, ["selfcheck", "--json", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["version"] == __version__
    assert any(item["key"] == "python" for item in payload["checks"])
