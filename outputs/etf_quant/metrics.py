"""绩效指标计算。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _annualize(ret: pd.Series, periods: int) -> float:
    if len(ret) < 2:
        return np.nan
    return float((1 + ret.mean()) ** periods - 1)


def performance(equity: pd.Series, trades: pd.DataFrame | None = None,
                periods: int = 244) -> dict:
    """equity 为日度净值序列（含现金）。periods=年化交易日数。"""
    if equity.empty or len(equity) < 2:
        return {}
    ret = equity.pct_change().dropna()
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
    ann = _annualize(ret, periods)
    vol = float(ret.std() * np.sqrt(periods))
    sharpe = float(ret.mean() / ret.std() * np.sqrt(periods)) if ret.std() > 0 else np.nan
    downside = ret[ret < 0]
    dstd = downside.std() if len(downside) > 0 else np.nan
    sortino = float(ret.mean() / dstd * np.sqrt(periods)) if dstd and dstd > 0 else np.nan

    cummax = equity.cummax()
    dd = equity / cummax - 1
    maxdd = float(dd.min())
    calmar = float(ann / abs(maxdd)) if maxdd < 0 else np.nan

    out = {
        "total_return": total_ret,
        "annual_return": ann,
        "annual_vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": maxdd,
        "n_obs": int(len(ret)),
    }
    if trades is not None and len(trades) > 0:
        wins = trades[trades["pnl"] > 0]
        losses = trades[trades["pnl"] <= 0]
        out["n_trades"] = int(len(trades))
        out["win_rate"] = float(len(wins) / len(trades))
        out["avg_win"] = float(wins["pnl"].mean()) if len(wins) else 0.0
        out["avg_loss"] = float(losses["pnl"].mean()) if len(losses) else 0.0
        plr = (abs(wins["pnl"].mean()) / abs(losses["pnl"].mean())
               if len(losses) and losses["pnl"].mean() != 0 else np.nan)
        out["profit_loss_ratio"] = plr
        out["avg_holding_days"] = float(trades["holding_days"].mean())
        out["avg_net_pnl"] = float(trades["pnl"].mean())
        out["total_cost"] = float(trades["cost"].sum())
        out["turnover"] = float(trades["gross_value"].sum())
    return out


def sub_period_table(equity: pd.Series, boundaries: dict[str, str]) -> pd.DataFrame:
    rows = []
    for label, (a, b) in boundaries.items():
        seg = equity.loc[a:b]
        if len(seg) >= 2:
            p = performance(seg)
            p["period"] = label
            rows.append(p)
    return pd.DataFrame(rows).set_index("period") if rows else pd.DataFrame()
