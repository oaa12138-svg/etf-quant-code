"""步骤 3：特征/评分/回测/验证，输出绩效与报告。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from etf_quant.config import BACKTEST, UNIVERSE
from etf_quant.data.prepare import (
    load_broad_meta, select_representatives, load_market_indices, load_etf_kline,
)
from etf_quant.indicators import compute_features, compute_market_env
from etf_quant.scoring import score_panel, build_panel, market_gate
from etf_quant.backtest import (
    run_signal_strategy, run_buy_hold, run_equal_weight, dynamic_dedupe,
)
from etf_quant.metrics import performance, sub_period_table


OUT = Path(__file__).resolve().parent


def load_representatives() -> pd.DataFrame:
    meta = load_broad_meta()
    return select_representatives(meta)


def build_data(rep: pd.DataFrame):
    cols = ["code", "index_key"] + (["broad_subtype"] if "broad_subtype" in rep.columns else [])
    meta = rep[cols].copy()
    meta["code"] = meta["code"].astype(str)
    features = {}
    klines = {}
    for _, r in rep.iterrows():
        code = str(r["code"])
        k = load_etf_kline(code)
        if k.empty:
            continue
        k = k.dropna(subset=["close_adj"])
        if len(k) < 60:
            continue
        klines[code] = k
        features[code] = compute_features(k)
    panel = build_panel(features).merge(meta, on="code", how="left")
    scored = score_panel(panel)
    return scored, klines


def main() -> None:
    print("加载宽基池与市场指数 ...", flush=True)
    meta = load_broad_meta()
    meta["code"] = meta["code"].astype(str)
    env_raw = load_market_indices()
    env = compute_market_env(env_raw)
    env["gate"] = market_gate(env)

    print("计算特征与评分 ...", flush=True)
    scored_all, _ = build_data(meta)
    scored = dynamic_dedupe(scored_all)

    calendar = env.index.intersection(pd.DatetimeIndex(scored["date"].unique())).sort_values()
    calendar = pd.DatetimeIndex(calendar)
    print(f"交易日历：{len(calendar)} 天（{calendar.min().date()} ~ {calendar.max().date()}）", flush=True)

    # 硬性流动性门槛下的可交易数量（截至最新）
    latest = scored[scored["date"] == scored["date"].max()]
    pass_filter = latest[latest["adv20"] >= UNIVERSE["min_adv20"]]
    print(
        f"宽基 {meta['code'].nunique()} 只；动态去重后最新日 {latest['code'].nunique()} 只；"
        f"通过硬性流动性门槛（20/60 日均成交额≥5亿）：{pass_filter['code'].nunique()} 只",
        flush=True,
    )

    def run_sig(rank_col, **kw):
        return run_signal_strategy(scored, env, calendar, rank_col=rank_col,
                                   initial_cash=BACKTEST["initial_cash"],
                                   price_panel=scored_all, **kw)

    def _bh_series(s: pd.Series):
        if s is None or s.empty:
            return None, pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float)
        cash = pd.Series(0.0, index=s.index)
        exp = pd.Series(1.0, index=s.index)
        return s, pd.DataFrame(), cash, exp

    strategies = {
        "买入持有(沪深300ETF)": _bh_series(run_buy_hold(scored_all, "510300", BACKTEST["initial_cash"])),
        "买入持有(中证500ETF)": _bh_series(run_buy_hold(scored_all, "510500", BACKTEST["initial_cash"])),
        "全宽基等权(不计成本)": _bh_series(run_equal_weight(scored, BACKTEST["initial_cash"])),
        "纯短期动量": run_sig("score_momentum", use_risk_filter=False,
                              use_market_gate=False, use_net_gate=False,
                              use_right_side=False),
        "动量+风险过滤": run_sig("score_momentum", use_risk_filter=True,
                                use_market_gate=True, use_net_gate=True),
        "多因子综合模型": run_sig("composite", use_risk_filter=True,
                                  use_market_gate=True, use_net_gate=True),
    }

    boundaries = {
        "训练": (str(calendar.min().date()), BACKTEST["train_end"]),
        "验证": (BACKTEST["train_end"], BACKTEST["val_end"]),
        "样本外测试": (BACKTEST["test_start"], str(calendar.max().date())),
    }

    rows = []
    for name, (eq, trades, cash, exp) in strategies.items():
        if eq is None or eq.empty:
            continue
        p = performance(eq, trades)
        p["strategy"] = name
        p["cash_share"] = float((cash / eq).mean())
        p["n_trade_days"] = int((exp > 0.001).sum())
        rows.append(p)

    perf = pd.DataFrame(rows).set_index("strategy")
    print("\n=== 全周期绩效 ===", flush=True)
    cols = ["annual_return", "max_drawdown", "annual_vol", "sharpe", "sortino",
            "calmar", "win_rate", "n_trades", "cash_share", "turnover"]
    print(perf[cols].to_string(float_format=lambda x: f"{x:,.4f}"), flush=True)

    # 样本外测试分段
    print("\n=== 分段绩效（年化收益 / 最大回撤 / 夏普 / 交易数）===", flush=True)
    seg_rows = []
    for name, (eq, trades, cash, exp) in strategies.items():
        if eq is None or eq.empty:
            continue
        for label, (a, b) in boundaries.items():
            seg = eq.loc[a:b]
            tr = trades[(trades["exit_date"] >= pd.Timestamp(a)) & (trades["exit_date"] <= pd.Timestamp(b))] if len(trades) else trades
            p = performance(seg, tr)
            p["strategy"] = name
            p["segment"] = label
            seg_rows.append(p)
    seg = pd.DataFrame(seg_rows).pivot_table(
        index="strategy", columns="segment",
        values=["annual_return", "max_drawdown", "sharpe", "n_trades"])
    print(seg.to_string(float_format=lambda x: f"{x:,.3f}"), flush=True)

    perf.to_csv(OUT / "backtest_performance.csv", encoding="utf-8-sig")
    # 保存多因子模型的净值与交易明细
    model = strategies["多因子综合模型"]
    model[0].rename("equity").to_csv(OUT / "equity_curve_model.csv", encoding="utf-8-sig")
    if len(model[1]):
        model[1].to_csv(OUT / "trades_model.csv", index=False, encoding="utf-8-sig")
    print("\n已保存 backtest_performance.csv / equity_curve_model.csv / trades_model.csv", flush=True)


if __name__ == "__main__":
    main()
