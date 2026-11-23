"""`python -m pre_sales_kit.demo` 的入口。

doctor.yaml 里的修复命令用的是这个形式：它不依赖 `{spec_dir}` 下有没有 demo_app.py，
换台机器、换个目录都还能起来。
"""

from __future__ import annotations

from pre_sales_kit.demo.app import main

if __name__ == "__main__":
    main()
