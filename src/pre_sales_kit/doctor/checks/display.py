"""显示检查：分辨率 + 浏览器版本。

投影仪常见 1024×768，演示界面写死 1280 宽就当场翻车。
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from pre_sales_kit.core.proc import run_capture
from pre_sales_kit.core.status import Status
from pre_sales_kit.core.text import parse_resolution
from pre_sales_kit.doctor.models import CheckContext, CheckSpec, Item
from pre_sales_kit.doctor.params import Params
from pre_sales_kit.doctor.registry import check

_ALLOWED = ("min_resolution", "browsers")

_BROWSER_LABELS = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox"}


@check("display", "显示：分辨率是否够用、浏览器是否就绪")
def run_display(spec: CheckSpec, ctx: CheckContext) -> list[Item]:  # noqa: ARG001 - ctx 是检查协议的一部分
    params = Params.of(spec, _ALLOWED)
    min_resolution = params.text("min_resolution", "1024x768")
    wanted_raw = params.sequence("browsers")
    wanted = [str(item).strip().lower() for item in wanted_raw] if wanted_raw else []
    unknown = [name for name in wanted if name not in _BROWSER_LABELS]
    if unknown:
        raise Params._spec_error(
            spec,
            "params.browsers",
            f"只支持 {', '.join(_BROWSER_LABELS)}；实际写了 {', '.join(unknown)}",
        )
    return [_check_resolution(min_resolution), _check_browsers(wanted)]


def _check_resolution(min_resolution: str | None) -> Item:
    label = "分辨率"
    if os.name != "nt":
        return Item(label, Status.SKIP, "自动检查仅支持 Windows；macOS / Linux 请手动确认投影分辨率")
    try:
        import ctypes

        # 取物理像素：不做 DPI 感知的话，缩放 150% 的笔记本会报出 1280×720 的假分辨率
        with contextlib.suppress(Exception):  # 老系统没有 shcore，忽略即可
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
    except Exception as exc:
        return Item(label, Status.WARN, f"读不到屏幕分辨率：{exc}")
    if width <= 0 or height <= 0:
        return Item(label, Status.WARN, "读到的分辨率是 0，可能没有连接显示器")

    min_width, min_height = parse_resolution(min_resolution or "1024x768")
    if width < min_width or height < min_height:
        return Item(
            label,
            Status.WARN,
            f"{width}×{height} 低于 {min_width}×{min_height}",
            hint="投影仪通常是 1024×768：演示页面别写死更宽的布局，先换上投影再看一眼",
        )
    return Item(label, Status.PASS, f"{width}×{height}（阈值 {min_width}×{min_height}）")


def _check_browsers(wanted: list[str]) -> Item:
    label = "浏览器"
    detected = _detect_browsers()
    if not detected:
        return Item(
            label,
            Status.WARN,
            "没有检测到 Chrome / Edge / Firefox",
            hint="客户机上至少要有一个能开的浏览器；无网现场别依赖在线安装",
        )
    names = wanted or sorted(detected)
    missing = [name for name in names if name not in detected]
    parts = []
    for name in names:
        if name in detected:
            version = detected[name]
            parts.append(f"{_BROWSER_LABELS[name]} {version}".strip())
    if missing:
        missing_text = "、".join(_BROWSER_LABELS[name] for name in missing)
        return Item(
            label,
            Status.WARN,
            f"未检测到 {missing_text}" + (f"；已有 {' · '.join(parts)}" if parts else ""),
            hint="演示用哪台机器，就在那台机器上补装",
        )
    return Item(label, Status.PASS, " · ".join(parts))


def _detect_browsers() -> dict[str, str]:
    if os.name == "nt":
        return _windows_browsers()
    if sys.platform == "darwin":
        return _mac_browsers()
    return _linux_browsers()


def _windows_browsers() -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return found

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    keywords = {
        "google chrome": "chrome",
        "microsoft edge": "edge",
        "mozilla firefox": "firefox",
    }
    for root, subkey in roots:
        try:
            key = winreg.OpenKey(root, subkey)
        except OSError:
            continue
        try:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    child_name = winreg.EnumKey(key, index)
                    child = winreg.OpenKey(key, child_name)
                except OSError:
                    continue
                with child:
                    display = _reg_str(child, "DisplayName").lower()
                    for keyword, browser in keywords.items():
                        if keyword in display and browser not in found:
                            version = _reg_str(child, "DisplayVersion")
                            if version:
                                found[browser] = version
        finally:
            key.Close()

    # Edge 是系统应用，可能不在卸载注册表里，兜一层固定路径
    fallback = {
        "edge": [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / r"Microsoft\Edge\Application\msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / r"Microsoft\Edge\Application\msedge.exe",
        ],
        "chrome": [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / r"Google\Chrome\Application\chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / r"Google\Chrome\Application\chrome.exe",
        ],
    }
    for browser, paths in fallback.items():
        if browser in found:
            continue
        if any(path.exists() for path in paths):
            found[browser] = "已安装（版本未知）"
    return found


def _reg_str(key, name: str) -> str:
    try:
        import winreg

        value, _kind = winreg.QueryValueEx(key, name)
        return str(value).strip()
    except OSError:
        return ""


def _mac_browsers() -> dict[str, str]:
    candidates = {
        "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "firefox": "/Applications/Firefox.app/Contents/MacOS/firefox",
    }
    return _probe_paths(candidates)


def _linux_browsers() -> dict[str, str]:
    candidates = {
        "chrome": "/usr/bin/google-chrome",
        "edge": "/usr/bin/microsoft-edge",
        "firefox": "/usr/bin/firefox",
    }
    return _probe_paths(candidates)


def _probe_paths(candidates: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for browser, path in candidates.items():
        if not Path(path).exists():
            continue
        try:
            _code, output = run_capture([path, "--version"], timeout=10)
        except Exception:
            found[browser] = "已安装（版本未知）"
            continue
        import re

        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", output)
        found[browser] = match.group(1) if match else "已安装（版本未知）"
    return found
