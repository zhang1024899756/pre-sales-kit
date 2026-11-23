# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
