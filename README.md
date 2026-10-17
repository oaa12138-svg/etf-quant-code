# 中国 A 股宽基 ETF 短线量化模型

全市场宽基 ETF 的自动发现、硬性过滤、量化排名、风险复核与人工确认工作流。
本仓库包含可运行的 Python 代码、回测/验证脚本、关键结果表和研究报告。

## 在线页面

- GitHub Pages：https://oaa12138-svg.github.io/etf-workflow/
- 页面仓库：https://github.com/oaa12138-svg/etf-workflow
- 本代码仓库：https://github.com/oaa12138-svg/etf-quant-code

页面每日更新后会由 `outputs/deploy_github.py` 发布到页面仓库。

## 项目结论（先看这个）

- 回撤控制：多因子模型全程最大回撤 **-3.91%**，样本外 **-3.9%**，
  同期买入持有基准约 **-16.8% ~ -29.2%**。
- 收益与稳健性不达标：全程年化 **+0.51%**，样本外 **+2.86%**，低于买入持有；
  成本放大 2 倍即 -0.93%/年。
- 全市场自动筛选在本窗口内没有跑赢固定 4 只池（固定池 +2.36% / -2.78% /
  夏普 0.748；动态池 +0.51% / -3.91% / 0.150）。
- V3 规则：20/60 日日均成交额 ≥5 亿元；只做右侧（trend20>0 且 SMA20>SMA60）；
  单笔风险预算 0.9%；1R 后按 2×ATR 移动止盈；现金按 1.5%/年计息。
- **结论：只适合纸面/模拟交易，不使用真实资金。**

完整证据见 [outputs/VALIDATION_REPORT.md](outputs/VALIDATION_REPORT.md)。

## 目录

```text
outputs/
  etf_quant/
    config.py        # 路径、成本、候选池门槛、回测参数、统一口径
    baseline.py      # 8 个基础指标（5/10 日动量、20 日趋势、相对成交额、ATR、波动、回撤、净预期收益）
    indicators.py    # 基础层 + 复杂因子
    scoring.py       # 横截面 z-score 与综合评分
    position.py      # 5 万元内风险预算仓位
    backtest.py      # 事件驱动回测（收盘信号 -> 次日开盘成交）
    metrics.py       # 绩效指标
    data/            # 数据源、分类、候选池、数据集
  run_validate_baseline.py  # 验证 8 个基础指标与无未来函数
  run_discover.py            # 全市场 ETF 发现与分类
  run_prepare.py             # 抓取日线、生成代表池
  run_backtest.py            # 特征/评分/回测/绩效
  run_sensitivity.py         # 参数与成本敏感性
  run_build_workflow.py      # 生成推荐工作流页面
  run_daily_update.py        # 日更入口（数据 + 页面 + GitHub Pages）
  deploy_github.py           # 通过 GitHub Contents API 发布页面
  workflow_template.html     # 页面模板
results/                     # 关键结果表（候选池、绩效、敏感性、交易明细）
```

## 快速开始

需要 Python 3.12、`pandas`、`numpy`；HTTP 使用标准库 `urllib`，不依赖 `requests`。

```powershell
python -m pip install -r requirements.txt
python outputs/run_validate_baseline.py
python outputs/run_discover.py
python outputs/run_prepare.py
python outputs/run_backtest.py
python outputs/run_sensitivity.py
python outputs/run_build_workflow.py
```

首次运行会在仓库根目录创建 `work/cache/`（已被 `.gitignore` 排除）。
`run_build_workflow.py` 输出 `outputs/recommendation_workflow.html`，该文件也已被
`.gitignore` 排除，因为它发布在独立的页面仓库中。

日更（收盘后）：

```powershell
python outputs/run_daily_update.py
```

日更会刷新基金目录/分类、宽基日线、市场指数、敏感性、页面，并尝试发布到
GitHub Pages。未配置 GitHub 凭据时，`daily_update_status.json` 会标记
`github: failed`，但本地数据与页面仍会更新。

## 统一口径

- 交易日：沪深300交易日历。
- 复权：前复权（总收益口径）。
- 收益率：简单收益率 `P_t/P_{t-k}-1`。
- 年化：日波动率 × √244。
- 相对成交额：当日 ÷ 过去 20 日（不含当日）均值。
- ATR：Wilder ATR(14)。
- 短期回撤：收盘 ÷ 20 日收盘高点 − 1。
- 净预期收益：10 日动量 − 双边成本（1 万元名义约 26bp）。

这些是研究起始定义，不是已验证的买卖条件；综合评分不是上涨概率。

## 数据与限制

- 数据源：东方财富基金目录/F10、腾讯行情；免费日线源约 800 根，回测窗口
  2023-06 ~ 2026-10。
- 候选池为当前快照，已退市 ETF 可能缺失；按“日线首日起进入池”处理上市时点，
  但无法完全消除退市幸存者偏差。
- 历史逐笔盘口不可得，价差/滑点用保守参数并做敏感性；真实冲击需模拟盘校准。
- 市场广度用主要宽基指数代理，非严格个股涨跌家数。
- 本项目仅供研究，不构成投资建议；未经验证不得使用真实资金。
