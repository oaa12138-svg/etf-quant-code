"""小资金（5 万元以内）仓位管理：风险预算 + 波动率调整 + 整手取整。"""

from __future__ import annotations

import math

from .config import COST


def size_position(equity: float, price: float, atr_pct: float,
                  risk_budget: float = 0.008, stop_atr: float = 2.0,
                  max_fraction: float = 0.50, min_trade_value: float = 3000.0,
                  lot: int = 100) -> int:
    """按风险预算决定买入份数。

    风险敞口 = equity * risk_budget；止损距离 = stop_atr * atr_pct；
    目标市值 = 风险敞口 / 止损距离；再受 max_fraction 与整手约束。
    """
    if price <= 0 or not np_finite(price) or not np_finite(atr_pct):
        return 0
    stop_pct = max(stop_atr * atr_pct, 0.02)   # 止损距离下限 2%
    risk_value = equity * risk_budget
    target_value = risk_value / stop_pct
    target_value = min(target_value, equity * max_fraction)
    if target_value < min_trade_value:
        return 0
    shares = math.floor(target_value / price / lot) * lot
    if shares * price < min_trade_value:
        return 0
    return shares


def np_finite(x: float) -> bool:
    import numpy as np
    return bool(np.isfinite(x))


def round_trade_cost(value: float, side: str = "buy") -> float:
    """单边交易成本（佣金 + 价差/滑点）。"""
    commission = max(value * COST["commission_rate"], COST["commission_min"])
    impact = value * (COST["half_spread_bps"] + COST["slippage_bps"]) / 10000.0
    return commission + impact
