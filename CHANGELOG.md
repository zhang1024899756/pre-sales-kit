# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-11-23

这一版补的是「装和用」：装完能自己确认装对了，装完有东西能直接跑，演示服务不用再手工拼命令。

### 新增

- **kit init**：导出三份可运行的示例（doctor / points / matrix）
  - 示例样板打进 wheel，从 PyPI 装出来不再依赖仓库里的 `examples/`
  - 已存在**不覆盖**；`--force` 覆盖前备份成 `.bak`
  - 导出后直接打印下一步命令（带用户实际的目录名）
- **kit selfcheck**：12 项安装自检
  - Python 与依赖版本、**安装元数据与代码版本是否一致**、报告模板与示例文件是否齐全、
    命令入口是否在 PATH、控制台编码、输出目录可写
  - `--json`；退出码 0 / 1
- **kit demo**：演示服务一键起、停、查、开
  - `up` / `down` / `status` / `open`；`--service role=port`、`--only`、`--db`、`--dir`、`--wait`
  - 状态记在 `.kit/demo.json`，每个服务的输出在 `.kit/demo-<role>.log`
  - **只停自己起的进程**：端口被陌生进程占用时只报告、不动手
- 文档：[`docs/安装.md`](docs/安装.md)（四种装法、内网离线安装、按症状排错）、
  [`docs/使用.md`](docs/使用.md)（六个命令的完整参考、YAML 字段表、脚本化）

### 变更

- 演示服务从 `examples/demo_app.py` 迁进包（`pre_sales_kit.demo`）；
  doctor 示例的修复命令改为 `{python} -m pre_sales_kit.demo`，不再依赖 `{spec_dir}` 下有文件
- `examples/` 不再入库，由 `kit init examples` 生成；样板的唯一副本在 `src/pre_sales_kit/scaffold/`
- doctor 示例的数据检查路径与 seed 的输出路径对齐（`examples/data/demo.db`），
  原来一个写 `data/demo.db` 一个读 `examples/data/demo.db`，第一次上手必踩
- `kit init` 打印的下一步命令改为「最短到达绿灯」的顺序：seed → demo up → doctor

### 修复

- **Windows 上 `python.exe` 是启动器时（应用商店别名等），Popen 返回的 PID 不是监听者。**
  `kit demo` 改为记录**真正监听端口**的 PID；否则 `status` 会说「这不是 kit 起的」，
  `down` 会杀错进程。这个 bug 是 `kit demo status` 自己报出来的
- `kit init` 打印的路径混用 `\` 与 `/`（改用 `Path.as_posix()`，跨平台一致）

### 工程

- 191 条测试（新增 `test_init` / `test_demo` / `test_selfcheck` 共 23 条）
- CI 增加 `kit selfcheck`、`kit init` 与导出冒烟

## [0.1.0] - 2026-11-23

### 新增

- **kit doctor**：声明式演示环境自检
  - 7 类检查：端口、HTTP 服务、演示数据、运行环境、版本、网络、显示
  - `--fix` 自动修复 + **修复后复检**，高风险动作需 `--yes`
  - 单文件 HTML 报告（零外部资源，可打印）
  - `--json` / `--only` / `--skip` / `--strict` / `--list-types`
- **kit seed**：物理合理的演示数据生成
  - 5 种模型：thermal / load_step / random_walk / triangle / counter
  - 量程硬约束 + 生成后物理自检（越界、单步跳变、计数回退、值被四舍五入成直线）
  - 异常注入：ramp / spike / dropout / drift，注入后仍满足物理约束
  - 输出：SQLite / CSV（UTF-8 BOM）/ JSONL / HTTP POST
  - 预览页：min/max 包络 + 均值折线 + 异常段标注
- **kit compat**：兼容性矩阵查询与导出
  - 任意维度（顶层键即维度）、显式组合优先
  - 渐进匹配：精确 → 包含 → 前缀 → 近似，近似会标注
  - 结论口径：支持 / 有条件支持 / 不支持 / 未收录，对应退出码 0/3/1/4
  - 导出单文件 HTML 与 Markdown 表格

### 工程

- 168 条测试（模型不变量、匹配、CLI 端到端、报告离线校验）
- `ruff` 与 `mypy` 全绿；CI 覆盖 Linux / macOS / Windows × Python 3.11 / 3.12
- 终端表格按中文终端宽度对齐（`℃`、`–` 等歧义宽度字符按两格计算）