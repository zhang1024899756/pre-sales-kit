"""检查参数的读取与校验。

所有取值都从 Params 走，好处是错误信息里一定带「哪条检查、哪个参数、该写什么」。
"""

from __future__ import annotations

from typing import Any, NoReturn

from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.core.text import parse_duration, parse_size
from pre_sales_kit.doctor.models import CheckSpec


class Params:
    """一条检查的 params 视图（只读）。"""

    def __init__(self, spec: CheckSpec, allowed: set[str]) -> None:
        unknown = sorted(set(spec.params) - allowed)
        if unknown:
            raise self._spec_error(
                spec,
                "params",
                f"出现未知参数 {', '.join(unknown)}；可用参数：{', '.join(sorted(allowed))}",
            )
        self._spec = spec
        self._data: dict[str, Any] = dict(spec.params)

    @classmethod
    def of(cls, spec: CheckSpec, allowed: Any) -> Params:
        return cls(spec, set(allowed))

    # ---------- 读取 ----------

    def raw(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def has(self, key: str) -> bool:
        return key in self._data and self._data[key] is not None

    def text(
        self,
        key: str,
        default: str | None = None,
        *,
        required: bool = False,
        choices: tuple[str, ...] | None = None,
    ) -> str | None:
        value = self._data.get(key, default)
        if value is None:
            if required:
                self._fail(key, "是必填项")
            return None
        if not isinstance(value, str):
            self._fail(key, f"应该是字符串，实际是 {type(value).__name__}")
        text = value.strip()
        if choices and text not in choices:
            self._fail(key, f"必须是 {' / '.join(choices)} 之一，实际是 {text!r}")
        return text

    def integer(
        self,
        key: str,
        default: int | None = None,
        *,
        required: bool = False,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int | None:
        value = self._data.get(key, default)
        if value is None:
            if required:
                self._fail(key, "是必填项")
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self._fail(key, f"应该是整数，实际是 {value!r}")
        if minimum is not None and value < minimum:
            self._fail(key, f"不能小于 {minimum}，实际是 {value}")
        if maximum is not None and value > maximum:
            self._fail(key, f"不能大于 {maximum}，实际是 {value}")
        return value

    def number(
        self,
        key: str,
        default: float | None = None,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float | None:
        value = self._data.get(key, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self._fail(key, f"应该是数字，实际是 {value!r}")
        number = float(value)
        if minimum is not None and number < minimum:
            self._fail(key, f"不能小于 {minimum}，实际是 {number}")
        if maximum is not None and number > maximum:
            self._fail(key, f"不能大于 {maximum}，实际是 {number}")
        return number

    def boolean(self, key: str, default: bool = False) -> bool:
        value = self._data.get(key, default)
        if not isinstance(value, bool):
            self._fail(key, f"应该是 true / false，实际是 {value!r}")
        return value

    def duration(self, key: str, default: float | None = None) -> float | None:
        value = self._data.get(key, default)
        if value is None:
            return None
        try:
            return parse_duration(value)
        except Exception as exc:
            self._fail(key, str(exc))

    def size(self, key: str, default: int | None = None) -> int | None:
        value = self._data.get(key, default)
        if value is None:
            return None
        try:
            return parse_size(value)
        except Exception as exc:
            self._fail(key, str(exc))

    def mapping(self, key: str) -> dict[str, Any]:
        value = self._data.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            self._fail(key, "应该是键值映射")
        return value

    def sequence(self, key: str) -> list[Any]:
        value = self._data.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            self._fail(key, "应该是列表")
        return value

    def count(self, key: str, default: int | None = None) -> int | None:
        from pre_sales_kit.core.text import parse_count

        value = self._data.get(key, default)
        if value is None:
            return None
        try:
            return parse_count(value)
        except Exception as exc:
            self._fail(key, str(exc))

    # ---------- 报错 ----------

    def _fail(self, key: str, message: str) -> NoReturn:
        raise self._spec_error(self._spec, f"params.{key}", message)

    @staticmethod
    def _spec_error(spec: CheckSpec, field: str, message: str) -> SpecError:
        return SpecError(
            message=f"检查「{spec.name}」（{spec.id}）的 {field} {message}",
            path=spec.source,
            field=field,
        )
