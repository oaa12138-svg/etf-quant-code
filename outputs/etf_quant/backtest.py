"""事件驱动回测引擎（无未来函数，收盘信号 -> 次一交易日开盘成交）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BACKTEST, COST, UNIVERSE
from .position import size_position, round_trade_cost


MAX_HOLD = 12          # 最长持有交易日（约两周）
MAX_VOL20_ANN = 0.40   # 20 日年化波动率上限（风险过滤）
MAX_ATR_PCT = 0.045    # ATR/价格上限


def dynamic_dedupe(panel: pd.DataFrame) -> pd.DataFrame:
    """每个交易日、每个跟踪指数只保留 trailing ADV20 最大的 ETF。

    只使用当日及之前的 adv20，不使用未来流动性，避免“今天最活跃的 ETF
    被选去交易整个历史”的前视偏差。
    """
    p = panel.dropna(subset=["adv20", "index_key"]).copy()
    p = p.sort_values(["date", "index_key", "adv20"],
                      ascending=[True, True, False])
    return p.drop_duplicates(["date", "index_key"], keep="first").reset_index(drop=True)


def _trade_cost(value: float) -> float:
    return round_trade_cost(value) + round_trade_cost(value)


def build_open_map(panel: pd.DataFrame) -> dict[str, pd.Series]:
    """code -> (date -> open_adj) 查找表，用于次日开盘成交。"""
    out = {}
    for code, g in panel.groupby("code"):
        s = g.set_index("date")["open_adj"].sort_index()
        out[code] = s
    return out


def run_signal_strategy(
    panel: pd.DataFrame,
    env: pd.DataFrame,
    calendar: pd.Index,
    rank_col: str = "composite",
    use_risk_filter: bool = True,
    use_market_gate: bool = True,
    max_positions: int = 1,
    max_hold: int = MAX_HOLD,
    stop_atr: float = 2.0,
    risk_budget: float = 0.008,
    max_fraction: float = 0.50,
    min_score: float = 0.0,
    top_k: int = 3,
    initial_cash: float = 50_000.0,
    cost_scale: float = 1.0,
    use_net_gate: bool = True,
    cash_yield: float = BACKTEST["cash_yield"],
    price_panel: pd.DataFrame | None = None,
):
    """返回 (equity, trades, cash, exposure)。"""
    price_panel = panel if price_panel is None else price_panel
    open_map = build_open_map(price_panel)
    by_date = {d: g.set_index("code") for d, g in panel.groupby("date")}
    price_by_date = {d: g.set_index("code") for d, g in price_panel.groupby("date")}
    gate = env["gate"] if "gate" in env else pd.Series(1.0, index=env.index)

    def cost_fn(value: float) -> float:
        return round_trade_cost(value) * cost_scale

    cash = initial_cash
    positions: dict[str, dict] = {}   # code -> {shares, entry_price, entry_date, stop_pct, atr_pct}
    pending: list[dict] = []          # 待执行订单（次日开盘）
    trades: list[dict] = []
    equity_hist: dict[pd.Timestamp, float] = {}
    cash_hist: dict[pd.Timestamp, float] = {}

    def close_of(code: str, d) -> float | None:
        g = price_by_date.get(d)
        if g is not None and code in g.index:
            v = g.loc[code, "close_adj"]
            return float(v) if pd.notna(v) else None
        return None

    def open_of(code: str, d) -> float | None:
        s = open_map.get(code)
        if s is not None and d in s.index:
            v = s.loc[d]
            return float(v) if pd.notna(v) else None
        return None

    def execute(day: pd.Timestamp):
        nonlocal cash, positions, pending
        executed = []
        for od in pending:
            code = od["code"]
            px = open_of(code, day)
            if px is None:
                px = close_of(code, day)
            if px is None:
                continue
            if od["side"] == "buy":
                shares = od["shares"]
                value = shares * px
                cost = cost_fn(value)
                if value + cost > cash:
                    shares = int(cash / (px * (1 + COST["commission_rate"] + 0.001)) // 100 * 100)
                    if shares <= 0:
                        continue
                    value = shares * px
                    cost = cost_fn(value)
                cash -= value + cost
                positions[code] = {
                    "shares": shares, "entry_price": px, "entry_date": day,
                    "stop_pct": od["stop_pct"], "atr_pct": od["atr_pct"],
                    "buy_cost": cost,
                }
                executed.append(od)
            else:
                pos = positions.get(code)
                if pos is None:
                    continue
                shares = min(od["shares"], pos["shares"])
                value = shares * px
                cost = cost_fn(value)
                total_cost = pos["buy_cost"] + cost
                cash += value - cost
                pnl = (px - pos["entry_price"]) * shares - total_cost
                trades.append({
                    "code": code, "entry_date": pos["entry_date"], "exit_date": day,
                    "shares": shares, "entry_price": pos["entry_price"],
                    "exit_price": px, "pnl": pnl, "cost": total_cost,
                    "holding_days": (day - pos["entry_date"]).days,
                    "gross_value": shares * pos["entry_price"],
                })
                pos["shares"] -= shares
                if pos["shares"] <= 0:
                    positions.pop(code)
                executed.append(od)
        pending = [o for o in pending if o not in executed]

    for i, day in enumerate(calendar):
        execute(day)
        # 现金收益：按年化 cash_yield 折算到每个交易日
        if cash_yield:
            cash *= (1.0 + cash_yield) ** (1.0 / 244.0)

        # 持仓市值（收盘）
        mv = 0.0
        for code, pos in positions.items():
            c = close_of(code, day)
            if c is None:
                c = pos["entry_price"]
            mv += pos["shares"] * c
        equity = cash + mv
        equity_hist[day] = equity
        cash_hist[day] = cash

        # 当日收盘生成信号 -> 次日开盘执行
        pending = []

        # 1) 退出信号（当日收盘判定）
        for code, pos in list(positions.items()):
            g = price_by_date.get(day)
            if g is None or code not in g.index:
                continue
            row = g.loc[code]
            c = row["close_adj"]
            sma10 = row.get("sma10")
            hold_days = (day - pos["entry_date"]).days
            exit_signal = False
            if hold_days >= max_hold:
                exit_signal = True
            elif pd.notna(sma10) and c < sma10:
                exit_signal = True
            elif c <= pos["entry_price"] * (1 - pos["stop_pct"]):
                exit_signal = True
            if exit_signal:
                pending.append({"code": code, "side": "sell", "shares": pos["shares"]})

        # 2) 入场信号
        if use_market_gate and gate.get(day, 1.0) != 1.0:
            pass
        else:
            n_open = sum(1 for o in pending if o["side"] == "sell")
            slots = max_positions - (len(positions) - n_open)
            if slots > 0 and cash > 1000:
                g = by_date.get(day)
                if g is not None:
                    cand = g.dropna(subset=[rank_col, "ret10", "vol20", "atr_pct", "adv20"])
                    cand = cand[cand.index.isin(set(open_map) & set(cand.index))]
                    held = set(positions)
                    cand = cand[~cand.index.isin(held)]
                    if use_risk_filter:
                        cand = cand[(cand["vol20"] <= MAX_VOL20_ANN) & (cand["atr_pct"] <= MAX_ATR_PCT)]
                    if use_net_gate:
                        cand = cand[cand["net_expected_return"] > 0]
                    cand = cand[cand["adv20"] >= UNIVERSE["min_adv20"]]
                    cand = cand[cand[rank_col] >= min_score].sort_values(rank_col, ascending=False)
                    if not cand.empty:
                        for code in cand.index[:top_k]:
                            if slots <= 0:
                                break
                            row = cand.loc[code]
                            atr_pct = float(row["atr_pct"])
                            stop_pct = max(stop_atr * atr_pct, 0.02)
                            price = float(row["close_adj"])
                            shares = size_position(
                                equity, price, atr_pct, risk_budget=risk_budget,
                                stop_atr=stop_atr, max_fraction=max_fraction)
                            if shares <= 0:
                                continue
                            pending.append({
                                "code": code, "side": "buy", "shares": shares,
                                "stop_pct": stop_pct, "atr_pct": atr_pct,
                            })
                            slots -= 1

    equity = pd.Series(equity_hist).sort_index()
    cash_s = pd.Series(cash_hist).sort_index()
    trades_df = pd.DataFrame(trades)
    exposure = pd.Series(1.0, index=equity.index) - cash_s / equity
    return equity, trades_df, cash_s, exposure


def run_buy_hold(panel: pd.DataFrame, code: str, initial_cash: float = 50_000.0) -> pd.Series:
    """买入持有基准（净值化，不计交易成本）。"""
    g = panel[panel["code"] == code].set_index("date")["close_adj"].sort_index().dropna()
    if g.empty:
        return pd.Series(dtype=float)
    return initial_cash * g / g.iloc[0]


def run_equal_weight(panel: pd.DataFrame, initial_cash: float = 50_000.0) -> pd.Series:
    """全宽基等权基准（日度等权收益，不计成本）。"""
    ret = panel.pivot_table(index="date", columns="code", values="ret1")
    return initial_cash * (1 + ret.mean(axis=1)).cumprod().fillna(1.0)
