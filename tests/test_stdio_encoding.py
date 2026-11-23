"""CLI 在非 UTF-8 控制台下的输出编码回归测试。

背景：英文版 Windows 的默认代码页是 cp1252，直接打印中文会抛 UnicodeEncodeError，
`kit selfcheck` 会在 GitHub Actions 的 windows-latest 上崩掉。
"""

from __future__ import annotations

import io
import sys

from pre_sales_kit.cli import _ensure_utf8_stdio


def test_cp1252_stream_can_print_chinese_after_setup() -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    original = sys.stdout
    sys.stdout = stream
    try:
        _ensure_utf8_stdio()
        stream.write("自检通过")
        stream.flush()
        assert "自检通过".encode() in buffer.getvalue()
    finally:
        sys.stdout = original
        stream.close()
