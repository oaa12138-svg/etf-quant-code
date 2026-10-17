"""步骤 4：参数敏感性 / 稳健性检验（多因子综合模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from etf_quant.config import BACKTEST
from etf_quant.data.prepare import load_broad_meta, load_market_indices
from etf_quant.indicators import compute_features, compute_market_env
from etf_quant.scoring import score_panel, build_panel, market_gate
from etf_quant.backtest import run_signal_strategy, dynamic_dedupe
from etf_quant.metrics import performance
from run_backtest import build_data


def main() -> None:
    meta = load_broad_meta()
    meta["code"] = meta["code"].astype(str)
    env = compute_market_env(load_market_indices())
    env["gate"] = market_gate(env)
    scored_all, _ = build_data(meta)
    scored = dynamic_dedupe(scored_all)
    cal = env.index.intersection(pd.DatetimeIndex(scored["date"].unique())).sort_values()
    cal = pd.DatetimeIndex(cal)

    def run(**kw):
        eq, tr, cash, exp = run_signal_strategy(
            scored, env, cal, rank_col="composite", use_risk_filter=True,
            use_market_gate=True, initial_cash=BACKTEST["initial_cash"],
            price_panel=scored_all, **kw)
        p = performance(eq, tr)
        p["cash_share"] = float((cash / eq).mean())
        return p

    cases = [
        ("基准", {}),
        ("最长持有=5日", {"max_hold": 5}),
        ("最长持有=10日", {"max_hold": 10}),
        ("最长持有=20日", {"max_hold": 20}),
        ("风险预算=0.5%", {"risk_budget": 0.005}),
        ("风险预算=1.5%", {"risk_budget": 0.015}),
        ("成本×2", {"cost_scale": 2.0}),
        ("成本×3", {"cost_scale": 3.0}),
        ("现金收益=0%", {"cash_yield": 0.0}),
        ("现金收益=2%", {"cash_yield": 0.02}),
        ("跳空缓冲=0.7", {"gap_buffer": 0.7}),
        ("跳空缓冲=1.0", {"gap_buffer": 1.0}),
        ("单ETF上限=100%", {"max_fraction": 1.0}),
        ("最大持仓=3只", {"max_positions": 3, "max_fraction": 0.4}),
    ]
    rows = []
    for name, kw in cases:
        try:
            p = run(**kw)
            p["case"] = name
            rows.append(p)
        except Exception as e:  # noqa: BLE001
            print(name, "ERR", e)
    df = pd.DataFrame(rows).set_index("case")
    cols = ["annual_return", "max_drawdown", "sharpe", "n_trades", "cash_share", "turnover"]
    print(df[cols].to_string(float_format=lambda x: f"{x:,.4f}"))
    df.to_csv(Path(__file__).resolve().parent / "sensitivity.csv", encoding="utf-8-sig")
    print("\n已保存 sensitivity.csv")


if __name__ == "__main__":
    main()
