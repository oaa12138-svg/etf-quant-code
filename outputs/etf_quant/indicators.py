"""指标层：以基础指标（baseline.py）为地基，再叠加复杂因子。

基础 8 项严格对齐研究起始定义；复杂因子全部只使用截至当日收盘的数据。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .baseline import compute_baseline


def compute_features(k: pd.DataFrame) -> pd.DataFrame:
    """单只 ETF 的日度特征。

    返回列包含：
      - 基础 8 项：mom5, mom10, trend20, rel_amount, atr_ratio, vol20, dd20,
        net_expected_return（见 baseline.py）
      - 复杂因子：ret3/ret20、均线斜率与相对位置、下行波动、跳空、收盘位置等
    """
    base = compute_baseline(k)
    kk = k.sort_values("date").set_index("date")
    c = kk["close_adj"].astype(float)
    o = kk["open_adj"].astype(float)
    h = kk["high_adj"].astype(float)
    l = kk["low_adj"].astype(float)
    amt = kk["amount"].astype(float)

    f = base.copy()

    # 兼容旧字段名（与基础定义一致）
    f["ret5"] = f["mom5"]
    f["ret10"] = f["mom10"]
    f["atr_pct"] = f["atr_ratio"]
    f["amt_ratio"] = f["rel_amount"]

    # 复杂/辅助因子（建立在基础层之上）
    f["ret3"] = c.pct_change(3)
    f["ret20"] = c.pct_change(20)
    f["sma5"] = c.rolling(5).mean()
    f["sma10"] = c.rolling(10).mean()
    f["sma60"] = c.rolling(60).mean()
    f["slope10"] = f["sma10"].pct_change(5)
    f["ma_spread"] = f["sma10"] / f["sma20"] - 1.0
    f["above20"] = (c > f["sma20"]).astype(float)
    f["above60"] = (c > f["sma60"]).astype(float)

    f["vol5"] = f["ret1"].rolling(5).std(ddof=1)
    f["vol10"] = f["ret1"].rolling(10).std(ddof=1)
    f["dvol20"] = f["ret1"].clip(upper=0).rolling(20).std(ddof=1)

    f["adv20"] = amt.rolling(20).mean()
    f["adv60"] = amt.rolling(60).mean()
    f["gap"] = o / c.shift() - 1.0
    rng = h - l
    f["close_pos"] = (c - l) / rng.replace(0, np.nan)
    f["hi60"] = c / c.rolling(60).max()

    f["close_adj"] = c
    f["open_adj"] = o
    return f


def compute_market_env(indices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """市场环境（日度）。用可获得的宽基指数做代理，不含个股涨跌家数。"""
    closes = pd.DataFrame({name: df.set_index("date")["close"] for name, df in indices.items()})
    closes = closes.sort_index()
    ret = closes.pct_change()

    env = pd.DataFrame(index=closes.index)
    if "沪深300" in closes:
        c300 = closes["沪深300"]
        env["mkt_ret5"] = c300.pct_change(5)
        env["mkt_ret20"] = c300.pct_change(20)
        env["mkt_above60"] = (c300 > c300.rolling(60).mean()).astype(float)
        env["mkt_vol20"] = ret["沪深300"].rolling(20).std(ddof=1) * np.sqrt(244)
    else:
        env["mkt_ret5"] = 0.0
        env["mkt_ret20"] = 0.0
        env["mkt_above60"] = 1.0
        env["mkt_vol20"] = np.nan

    env["breadth5"] = (ret.rolling(5).mean() > 0).mean(axis=1)
    if "中证1000" in closes and "沪深300" in closes:
        env["small_vs_large"] = (closes["中证1000"] / closes["沪深300"]).pct_change(20)
    else:
        env["small_vs_large"] = 0.0
    env["eq_ret5"] = ret.rolling(5).mean().mean(axis=1)
    return env
