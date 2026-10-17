# 中国 A 股全市场宽基 ETF 短线量化交易模型

可解释、可复现、可回测、可审计的短线研究框架。核心目标：**优先控制回撤，
其次提高扣除交易成本后的风险调整收益**。资金规模 ≤ 5 万元，执行方式为
“AI 生成信号与交易计划 → 人工审核 → 人工执行”。

## 运行环境

- Python 3.12（Codex 内置运行时即可）。
- 依赖：`pandas`、`numpy`（内置已带）；HTTP 用标准库 `urllib`，不需要 `requests`。
- 网络：需要访问东方财富/腾讯公开行情接口。

## 统一口径（全项目生效）

| 口径 | 定义 |
|---|---|
| 交易日 | 沪深300交易日历 |
| 复权 | 前复权（总收益口径） |
| 收益率 | 简单收益率 `P_t/P_{t-k}-1` |
| 年化 | 日波动率 × √244 |
| 相对成交额 | 当日 ÷ 过去 20 日（不含当日）均值 |
| ATR | Wilder ATR(14) |
| 短期回撤 | 收盘 ÷ 20 日收盘高点 − 1 |

这些口径写在 `etf_quant/config.py` 的 `CONVENTIONS` 与 `etf_quant/baseline.py` 中。

## 基础指标（8 项）

`etf_quant/baseline.py` 实现并验证：

1. 5 日动量 `close_t/close_{t-5}-1`
2. 10 日动量 `close_t/close_{t-10}-1`
3. 20 日趋势 `close_t/SMA20_t-1`
4. 相对成交额 `amount_t/mean(amount_{t-20..t-1})`
5. ATR 比率 `ATR(14)/close`
6. 20 日波动率 `std(ret,20)×√244`
7. 短期回撤 `close_t/max(close,20)-1`
8. 净预期收益 `expected_return - round_trip_cost_rate`

注意：这些是**研究起始定义**，不是已验证的买卖条件；“预期收益”起始代理为 10 日
动量，不是上涨概率。

## 复现流程

```powershell
$PY = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $PY -u outputs/run_validate_baseline.py   # 0) 验证 8 个基础指标与无未来函数
& $PY -u outputs/run_discover.py            # 1) 发现并分类全市场 ETF
& $PY -u outputs/run_prepare.py             # 2) 抓取日线、生成代表池
& $PY -u outputs/run_backtest.py            # 3) 特征/评分/回测/绩效
& $PY -u outputs/run_sensitivity.py         # 4) 参数敏感性
```

数据缓存在 `work/cache/`，二次运行不重复抓取。

## 每日更新

收盘后运行一条命令即可刷新数据并重建页面：

```powershell
& $PY -u outputs/run_daily_update.py
```

它会依次完成：

1. 刷新基金目录/分类（新上市 ETF 自动纳入）。
2. 全量刷新宽基 ETF 日线（约 2 分钟）。
3. 刷新市场指数。
4. 重算参数敏感性。
5. 重建 `recommendation_workflow.html`。
6. 写入 `daily_update_status.json`（数据日期、耗时、错误）。

可选参数：`--skip-universe`（跳过基金目录，适合每周跑一次）、
`--skip-sensitivity`、`--skip-page`、`--fast`（只刷新少量 ETF 做连通性测试）。

建议在 A 股收盘后、数据源完成更新再运行（通常 16:30 之后）。免费源有频率限制，
脚本按顺序抓取并带重试；部分失败时状态会标为 `partial`，旧缓存保留。

## 每日自动更新

已在 Codex 应用中创建“ETF 宽基工作流每日更新”自动化：每个交易日 18:00
（Asia/Shanghai）运行 `run_daily_update.py`，检查 `daily_update_status.json`，
失败或数据日期未前进时提醒；若本机服务/隧道未运行会尝试重启。该自动化依赖
Codex 应用处于运行状态；若需要完全脱离 Codex 的云端定时任务，需把脚本部署到
一台常开服务器或云函数。

## 手机 / 其他设备访问

**GitHub Pages（固定公网链接，推荐）：**

```text
https://oaa12138-svg.github.io/etf-workflow/
```

仓库：`https://github.com/oaa12138-svg/etf-workflow`。每日更新完成后
`outputs/deploy_github.py` 会把最新页面作为 `index.html` 上传；GitHub Pages
通常 1 分钟内完成重建。该仓库是公开的，页面内容也会公开。

**同一 Wi-Fi（稳定，需要电脑开机且服务运行）：**

```text
http://192.168.1.10:8765/recommendation_workflow.html
```

**任意网络（临时公网隧道，当前地址）：**

```text
https://7c88c74d64ffe0.lhr.life/recommendation_workflow.html
```

公网隧道地址会随隧道重启变化。获取最新地址：

```powershell
powershell -File outputs/show_tunnel_url.ps1
```

启动本地服务与隧道：

```powershell
pythonw.exe outputs/start_services.py
```

如果手机在同一 Wi-Fi 打不开，通常是 Windows 防火墙拦截了 8765 入站端口；
以管理员身份运行一次：

```powershell
New-NetFirewallRule -DisplayName "ETF Workflow 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

需要**永久固定**的公网链接时，建议把 `recommendation_workflow.html` 部署到
GitHub Pages / Cloudflare Pages / Netlify 等静态托管（需要相应账号）；临时隧道
不适合作为长期书签。

## 目录结构

```
outputs/
  etf_quant/
    config.py                # 路径/成本/门槛/回测参数/CONVENTIONS
    baseline.py              # 8 个基础指标 + 统一口径
    indicators.py            # 基础层 + 复杂因子
    scoring.py               # 评分与排名
    position.py              # 5 万元内仓位管理
    backtest.py              # 事件驱动回测引擎
    metrics.py               # 绩效指标
    data/                    # 数据源、分类、候选池、数据集
  run_validate_baseline.py   # 基础指标验证
  run_discover.py / run_prepare.py / run_backtest.py / run_sensitivity.py
  VALIDATION_REPORT.md       # 验证报告（结论与证据）
  report_daily_template.md   # 每日人工审核模板
  *.csv                      # 样本/候选池/绩效/净值/交易明细
```

## 关键结论

详见 `VALIDATION_REPORT.md`。摘要：

- 回撤控制：多因子模型全程最大回撤 **-3.91%**、样本外 **-3.9%**（基准 -16.8%~-29.2%）。
- 收益不达标：全程年化 **+0.51%**、样本外 **+2.86%**，低于买入持有；成本 ×2 即 -0.93%。
- 自动筛选未跑赢固定池：固定 4 只池（+2.36% / -2.78% / 夏普 0.748）优于动态池
  （+0.51% / -3.91% / 0.150）。
- V3 规则：20/60 日日均成交额 ≥5 亿元；只做右侧（trend20>0 且 SMA20>SMA60）；
  单笔风险预算 0.9%；1R 后按 2×ATR 移动止盈；现金按 1.5%/年计息。
- 结论：**只做纸面/模拟交易，不使用真实资金。**

## 诚实性声明

- 不伪造行情或回测结果；所有数字来自真实抓取数据与本地引擎。
- 未取得的数据（盘口、个股涨跌家数、退市 ETF 历史、更早历史）均已披露缺口与替代。
- 综合评分不是买入信号，也不代表上涨概率；权重为研究假设，非拟合参数。
