"""kit init：示例导出。

重点不是「文件有没有写出来」，而是：导出的 YAML 能不能被三个工具真正加载，
以及「已经改过的文件不覆盖」这条边界有没有守住。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from pre_sales_kit import scaffold
from pre_sales_kit.cli import app

runner = CliRunner()


def test_exports_three_files(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 0, result.output
    for name in scaffold.FILES:
        assert (target / name).is_file()


def test_exported_specs_are_accepted_by_every_tool(tmp_path: Path) -> None:
    """导出的样板必须是「能直接跑」的，不是给人看的片段。"""
    from pre_sales_kit.compat.spec import load_matrix
    from pre_sales_kit.doctor.spec import load_spec
    from pre_sales_kit.seed.spec import load_seed_spec

    target = tmp_path / "examples"
    runner.invoke(app, ["init", str(target)])

    assert load_spec(target / "doctor.yaml").checks
    assert load_seed_spec(target / "points.yaml").points
    assert load_matrix(target / "matrix.yaml").dimensions


def test_second_run_keeps_user_edits(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    runner.invoke(app, ["init", str(target)])
    edited = target / "doctor.yaml"
    mine = "# 我在现场改过，别覆盖\nversion: 1\nchecks:\n  - {name: 端口, type: port}\n"
    edited.write_text(mine, encoding="utf-8")

    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 0, result.output
    assert edited.read_text(encoding="utf-8") == mine
    assert not (target / "doctor.yaml.bak").exists()


def test_force_overwrites_and_keeps_backup(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    runner.invoke(app, ["init", str(target)])
    edited = target / "points.yaml"
    edited.write_text("# 改过\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(target), "--force"])
    assert result.exit_code == 0, result.output
    assert edited.read_text(encoding="utf-8") == scaffold.read_text("points.yaml")
    backup = target / "points.yaml.bak"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "# 改过\n"


def test_identical_file_is_not_reported_as_skipped(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    runner.invoke(app, ["init", str(target)])
    again = runner.invoke(app, ["init", str(target)])
    assert again.exit_code == 0
    assert "保留原文件" not in again.output


def test_only_exports_a_subset(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    result = runner.invoke(app, ["init", str(target), "--only", "doctor.yaml"])
    assert result.exit_code == 0, result.output
    assert (target / "doctor.yaml").is_file()
    assert not (target / "points.yaml").exists()


def test_unknown_file_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path), "--only", "nope.yaml"])
    assert result.exit_code == 2
    assert "未知" in result.output


def test_list_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    result = runner.invoke(app, ["init", str(target), "--list"])
    assert result.exit_code == 0
    assert "doctor.yaml" in result.output
    assert not target.exists()


def test_next_steps_use_given_directory(tmp_path: Path) -> None:
    steps = scaffold.next_steps(tmp_path / "demo")
    commands = [command for command, _note in steps]
    assert any("demo/doctor.yaml" in command for command in commands)
    assert all(command.startswith("kit ") for command in commands)
