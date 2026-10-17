"""信号评分与自动排名（横截面 z-score + 先验权重）。

权重是“研究假设”，不是拟合参数；通过训练/验证/测试切分与敏感性分析检验其稳健性。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BACKTEST

# 综合排名权重（先验假设）
WEIGHTS = {
    "momentum": 0.30,
    "trend": 0.25,
    "volume": 0.10,
    "risk": 0.20,
    "cost": 0.15,
}


def build_panel(features: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for code, f in features.items():
        g = f.reset_index().rename(columns={"index": "date"})
        g["code"] = code
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def _zscore(df: pd.DataFrame, col: str) -> pd.Series:
    g = df.groupby("date")[col]
    mu = g.transform("mean")
    sd = g.transform("std").replace(0, np.nan)
    return (df[col] - mu) / sd


def score_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """为每个 (日期, ETF) 计算子评分与综合评分（横截面标准化）。"""
    p = panel.copy()
    for col in ["ret10", "ret20", "trend20", "ma_spread", "slope10", "hi60",
                "amt_ratio", "close_pos", "vol20", "atr_pct", "dd20",
                "net_expected_return"]:
        p["z_" + col] = _zscore(p, col)

    # 风险子评分：低波动、低 ATR、小回撤 -> 高分
    p["score_risk"] = -(p["z_vol20"] + p["z_atr_pct"] + p["z_dd20"]) / 3.0
    p["score_momentum"] = (p["z_ret10"] + p["z_ret20"]) / 2.0
    # 趋势评分使用基础定义 trend20（收盘相对 20 日均线偏离），并配合斜率与高点距离；
    # ma_spread 仍保留为特征，但不重复计入综合分，避免同一趋势信号重复加分。
    p["score_trend"] = (p["z_trend20"] + p["z_slope10"] + p["z_hi60"]) / 3.0
    p["score_volume"] = (p["z_amt_ratio"] + p["z_close_pos"]) / 2.0
    p["score_cost"] = _zscore(p, "adv20")  # 成交额越大 -> 成本越低 -> 分越高

    p["composite"] = (
        WEIGHTS["momentum"] * p["score_momentum"]
        + WEIGHTS["trend"] * p["score_trend"]
        + WEIGHTS["volume"] * p["score_volume"]
        + WEIGHTS["risk"] * p["score_risk"]
        + WEIGHTS["cost"] * p["score_cost"]
    )
    return p


def market_gate(env: pd.DataFrame) -> pd.Series:
    """市场环境门控：返回每个交易日的“是否允许新增风险敞口”。"""
    gate = pd.Series(1.0, index=env.index)
    if "mkt_ret20" in env and "mkt_above60" in env:
        # 大盘 20 日下跌且位于 60 日均线下方 -> 暂停新增
        gate = ((env["mkt_above60"] > 0) | (env["mkt_ret20"] > -0.02)).astype(float)
    return gate


def rank_daily(scored: pd.DataFrame, min_score: float = 0.0) -> pd.DataFrame:
    """按日排名，返回每个日期的候选 ETF 排序（仅综合分 >= min_score）。"""
    out = []
    for date, g in scored.groupby("date"):
        g = g.dropna(subset=["composite"]).sort_values("composite", ascending=False)
        g = g[g["composite"] >= min_score]
        if not g.empty:
            g = g.copy()
            g["rank"] = range(1, len(g) + 1)
            out.append(g)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
