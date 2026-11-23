"""可运行的示例脚手架 —— `kit init` 把它们落到本地目录。

为什么放在包里而不是仓库的 examples/：
从 PyPI 装出来的人没有仓库，README 里第一条命令就断在这里。
放进来之后，`pip install pre-sales-kit && kit init` 才算真的装完就能用。
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from pre_sales_kit.core.errors import KitError

PACKAGE = "pre_sales_kit.scaffold"

#: 按「先看哪个」的顺序排
FILES: tuple[str, ...] = ("doctor.yaml", "points.yaml", "matrix.yaml")

DESCRIPTION = {
    "doctor.yaml": "演示环境自检声明（11 项检查，含自动修复命令）",
    "points.yaml": "点位模型（五种物理模型，量程即约束）",
    "matrix.yaml": "兼容性矩阵（三个维度 + 显式组合）",
}

#: 导出后给的三条下一步命令的顺序说明
NEXT_STEPS = (
    ("kit seed -m {points} -o {data} --interval 5s", "生成物理合理的演示数据"),
    ("kit demo up --db {data}", "拉起演示服务（读同一份数据）"),
    ("kit doctor {doctor} --html out/report.html", "体检并出报告（这一步应该全绿）"),
    ("kit doctor {doctor} --list-types", "看还有哪些检查类型可以加"),
    ("kit compat check -m {matrix} --os \"Windows Server 2016\" --db \"Oracle 11g\"", "查一个组合的兼容性"),
)


@dataclass(frozen=True)
class ExportResult:
    """一个示例文件的导出结果。"""

    name: str
    path: Path
    #: created / overwritten / identical / skipped
    action: str
    #: 给人看的一句话（skipped 时说明为什么没写）
    detail: str = ""

    @property
    def written(self) -> bool:
        return self.action in {"created", "overwritten"}


def available() -> list[str]:
    """安装包里实际存在的示例文件。"""
    root = resources.files(PACKAGE)
    return [name for name in FILES if root.joinpath(name).is_file()]


def read_text(name: str) -> str:
    if name not in FILES:
        raise KitError(
            f"没有名为 {name!r} 的示例文件",
            hint=f"可用的有：{'、'.join(FILES)}",
        )
    try:
        return resources.files(PACKAGE).joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise KitError(
            f"示例文件 {name} 不在安装包里",
            hint="安装包不完整，重新安装一次：pip install --force-reinstall pre-sales-kit",
        ) from exc


def export(
    target: Path | str,
    *,
    names: list[str] | tuple[str, ...] | None = None,
    force: bool = False,
) -> list[ExportResult]:
    """把示例写到 target 目录。

    已有的文件默认**不覆盖**（现场机器上的 YAML 可能已经被改过，那才是真的资产），
    只有 --force 才覆盖，并且在被覆盖的文件旁边留一份 .bak。
    """
    directory = Path(target)
    chosen = list(names) if names else list(FILES)
    unknown = [name for name in chosen if name not in FILES]
    if unknown:
        raise KitError(
            f"未知的示例文件：{'、'.join(unknown)}",
            hint=f"可用的有：{'、'.join(FILES)}",
        )

    directory.mkdir(parents=True, exist_ok=True)
    results: list[ExportResult] = []
    for name in chosen:
        content = read_text(name)
        path = directory / name
        if path.exists():
            existing = path.read_text(encoding="utf-8", errors="replace")
            if existing == content:
                results.append(ExportResult(name, path, "identical", "已经是这个样子了"))
                continue
            if not force:
                results.append(
                    ExportResult(name, path, "skipped", "已存在且内容不同，保留原文件（要覆盖加 --force）")
                )
                continue
            backup = path.with_name(path.name + ".bak")
            backup.write_text(existing, encoding="utf-8")
            path.write_text(content, encoding="utf-8")
            results.append(ExportResult(name, path, "overwritten", f"原文件备份为 {backup.name}"))
            continue
        path.write_text(content, encoding="utf-8")
        results.append(ExportResult(name, path, "created"))
    return results


def next_steps(directory: Path | str) -> list[tuple[str, str]]:
    """导出之后该敲的命令；路径带上用户实际用的目录。"""
    base = Path(directory)
    as_text = base.as_posix()
    prefix = "" if as_text in {".", ""} else f"{as_text}/"
    values = {
        "doctor": f"{prefix}doctor.yaml",
        "points": f"{prefix}points.yaml",
        "matrix": f"{prefix}matrix.yaml",
        "data": f"{prefix}data/demo.db",
    }
    return [(command.format(**values), note) for command, note in NEXT_STEPS]
