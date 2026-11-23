"""检查类型注册表。

加一类检查 = 写一个函数 + 打个 @check("type")，不用改 CLI、报告或配置格式。
"""

from __future__ import annotations

from collections.abc import Callable

from pre_sales_kit.core.errors import SpecError
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item

CheckFunc = Callable[[CheckSpec, CheckContext], list[Item]]

_REGISTRY: dict[str, CheckFunc] = {}
_DESCRIPTIONS: dict[str, str] = {}


def check(type_name: str, description: str = "") -> Callable[[CheckFunc], CheckFunc]:
    def decorator(func: CheckFunc) -> CheckFunc:
        if type_name in _REGISTRY:
            raise RuntimeError(f"检查类型重复注册：{type_name}")
        _REGISTRY[type_name] = func
        _DESCRIPTIONS[type_name] = description or (func.__doc__ or "").strip().splitlines()[0]
        return func

    return decorator


def get(type_name: str) -> CheckFunc:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        available = "、".join(sorted(_REGISTRY))
        raise SpecError(
            message=f"未知的检查类型 {type_name!r}；可用类型：{available}",
            hint="检查类型由 pre_sales_kit/doctor/checks/ 下的 @check 装饰器注册",
        ) from None


def known_types() -> list[str]:
    return sorted(_REGISTRY)


def describe(type_name: str) -> str:
    return _DESCRIPTIONS.get(type_name, "")


def load_builtin_checks() -> None:
    """导入内置检查模块，触发注册。"""
    from pre_sales_kit.doctor.checks import (  # noqa: F401
        data,
        display,
        env,
        network,
        ports,
        services,
        version,
    )
