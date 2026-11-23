"""工具自己的异常类型。

原则：使用者看到错误时，必须知道「哪个文件、第几行、哪个字段、怎么改」。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KitError(Exception):
    """所有可预期错误的基类；CLI 顶层统一捕获并渲染，不打印 traceback。"""

    message: str
    hint: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass
class SpecError(KitError):
    """YAML 声明文件写错了：给出文件、行号、路径与修改建议。"""

    path: str = ""
    line: int | None = None
    field: str | None = None

    def __str__(self) -> str:
        where = self.path
        if self.line:
            where += f":{self.line}"
        if self.field:
            where += f" [{self.field}]"
        return f"{where} {self.message}" if where else self.message


class CheckError(KitError):
    """单条检查执行失败（不影响其他检查）。"""


class FixError(KitError):
    """修复动作失败。"""


def spec_error(
    message: str,
    *,
    path: str | None = None,
    line: int | None = None,
    field: str | None = None,
    hint: str | None = None,
) -> SpecError:
    return SpecError(message=message, path=path or "", line=line, field=field, hint=hint)
