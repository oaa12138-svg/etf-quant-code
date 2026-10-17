"""基础指标层（严格对齐图中的 8 个起始研究定义）。

统一口径
--------
- 交易日：统一对齐到沪深300交易日历（master calendar）。
- 复权：价格类指标一律使用**前复权**价（总收益口径，分红视为再投资）。
- 收益率：简单收益率 ``P_t / P_{t-k} - 1``，不使用对数收益。
- 年化：日波动率 × ``sqrt(244)``；A 股一年按 244 个交易日。

注意
----
这些是**研究起始定义**，不是已验证的买卖条件。尤其“净预期收益”里的
“预期收益”只是 ``mom10`` 这一明确标注的代理，不是上涨概率或预测。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import COST, CONVENTIONS


@dataclass(frozen=True)
class Conventions:
    adjustment: str = CONVENTIONS["adjustment"]
    return_type: str = CONVENTIONS["return_type"]
    annualization_days: int = CONVENTIONS["annualization_days"]
    rel_amount_window: int = CONVENTIONS["rel_amount_window"]
    rel_amount_include_today: bool = CONVENTIONS["rel_amount_include_today"]
    atr_window: int = CONVENTIONS["atr_window"]
    atr_method: str = CONVENTIONS["atr_method"]
    vol_window: int = CONVENTIONS["vol_window"]
    vol_ddof: int = CONVENTIONS["vol_ddof"]
    drawdown_window: int = CONVENTIONS["drawdown_window"]


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series,
               window: int = 14) -> pd.Series:
    """Wilder ATR。

    TR_t = max(H_t-L_t, |H_t-C_{t-1}|, |L_t-C_{t-1}|)
    ATR_t = (ATR_{t-1}*(n-1) + TR_t) / n，首值取前 n 个 TR 的算术平均。
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    out = pd.Series(np.nan, index=close.index, dtype=float)
    if len(tr) < window:
        return out
    out.iloc[window - 1] = tr.iloc[:window].mean()
    for i in range(window, len(tr)):
        out.iloc[i] = (out.iloc[i - 1] * (window - 1) + tr.iloc[i]) / window
    return out


def round_trip_cost_rate(notional: float = CONVENTIONS["cost_notional"],
                         cost: dict | None = None) -> float:
    """全部预估交易成本（双边），以占成交金额的比例表示。

    单边 = max(佣金率, 最低佣金/成交额) + 价差/2 + 滑点。
    其中 ETF 免印花税、免过户费。
    """
    c = COST if cost is None else cost
    if notional <= 0:
        raise ValueError("notional must be positive")
    per_side_commission = max(c["commission_rate"], c["commission_min"] / notional)
    per_side_impact = (c["half_spread_bps"] + c["slippage_bps"]) / 10000.0
    return 2.0 * (per_side_commission + per_side_impact)


def compute_baseline(k: pd.DataFrame,
                     conv: Conventions | None = None,
                     notional: float | None = None) -> pd.DataFrame:
    """输入单只 ETF 的日线（需含 date/open_adj/high_adj/low_adj/close_adj/amount），
    返回 8 个基础指标及必要中间量。
    """
    conv = conv or Conventions()
    notional = CONVENTIONS["cost_notional"] if notional is None else notional

    k = k.sort_values("date").set_index("date")
    c = k["close_adj"].astype(float)
    h = k["high_adj"].astype(float)
    l = k["low_adj"].astype(float)
    amt = k["amount"].astype(float)

    out = pd.DataFrame(index=k.index)

    # 1/2 短期动量：P_t / P_{t-k} - 1
    out["mom5"] = c / c.shift(5) - 1.0
    out["mom10"] = c / c.shift(10) - 1.0

    # 3 20 日趋势：收盘价相对 20 日均线的偏离率
    sma20 = c.rolling(20).mean()
    out["sma20"] = sma20
    out["trend20"] = c / sma20 - 1.0

    # 4 相对成交额：当日成交额 / 过去 20 日平均成交额
    w = conv.rel_amount_window
    if conv.rel_amount_include_today:
        base_amt = amt.rolling(w).mean()
    else:
        base_amt = amt.shift(1).rolling(w).mean()
    out["rel_amount"] = amt / base_amt

    # 5 ATR 比率：ATR(14) / 收盘价
    atr = wilder_atr(h, l, c, window=conv.atr_window)
    out["atr14"] = atr
    out["atr_ratio"] = atr / c

    # 6 20 日波动率：最近 20 日简单收益率标准差 × sqrt(244)
    ret1 = c.pct_change()
    out["ret1"] = ret1
    out["vol20_daily"] = ret1.rolling(conv.vol_window).std(ddof=conv.vol_ddof)
    out["vol20"] = out["vol20_daily"] * np.sqrt(conv.annualization_days)

    # 7 短期回撤：当前价相对 20 日收盘高点的跌幅（≤0）
    out["high20"] = c.rolling(conv.drawdown_window).max()
    out["dd20"] = c / out["high20"] - 1.0

    # 8 净预期收益：预期收益 - 全部预估交易成本
    #    “预期收益”起始代理 = mom10（明确标注，不是概率）
    out["expected_return"] = out["mom10"]
    out["cost_rate"] = round_trip_cost_rate(notional=notional)
    out["net_expected_return"] = out["expected_return"] - out["cost_rate"]

    out["close_adj"] = c
    return out


def align_to_calendar(df: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """把指标对齐到统一交易日历。

    停牌/缺失日保留 NaN（不跨日填充价格），避免把停牌期伪装成连续交易。
    """
    return df.reindex(pd.DatetimeIndex(calendar)).sort_index()
