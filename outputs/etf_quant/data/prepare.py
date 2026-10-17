"""数据集构建：抓取并缓存宽基 ETF 与市场指数日线，生成元信息与代表池。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import time

from ..config import BACKTEST, MARKET_INDICES
from .classify_rules import canonical_index
from .eastmoney import fetch_kline_with_adjust, fetch_kline
from .store import load_kline, save_kline, load_csv, save_csv
from .universe import load_universe


def _adv(df: pd.DataFrame, n: int) -> float:
    if df.empty or "amount" not in df:
        return 0.0
    return float(df["amount"].tail(n).mean()) if len(df) >= 5 else 0.0


def load_etf_kline(code: str, start: str = BACKTEST["start"],
                   force: bool = False) -> pd.DataFrame:
    """加载单只 ETF 的复权日线；force=True 时重新抓取并覆盖缓存。"""
    if not force:
        df = load_kline(code)
        if df is not None and not df.empty:
            return df
    df = fetch_kline_with_adjust(f"{1 if not code.startswith('15') else 0}.{code}",
                                 start=start)
    if not df.empty:
        save_kline(code, df)
    return df


def load_market_indices(start: str = BACKTEST["start"],
                        force: bool = False) -> dict[str, pd.DataFrame]:
    """市场环境指数日线（前复权），缓存；force=True 时重新抓取。"""
    out: dict[str, pd.DataFrame] = {}
    for name, secid in MARKET_INDICES.items():
        code = secid.split(".")[-1]
        df = None if force else load_kline("idx_" + code)
        if df is None or df.empty:
            df = fetch_kline(secid, start=start, fqt=1)
            if not df.empty:
                save_kline("idx_" + code, df)
        if not df.empty:
            out[name] = df
    return out


def build_broad_meta(refresh: bool = False, force_klines: bool = False,
                     max_codes: int | None = None,
                     save: bool = True) -> pd.DataFrame:
    """抓取全部宽基 ETF 日线，计算流动性/历史元信息。"""
    univ = load_universe()
    broad = univ[univ["category"] == "broad"].copy()
    codes = sorted(broad["code"].astype(str).unique())
    if max_codes:
        codes = codes[:max_codes]

    meta_rows = []

    def _one(code):
        df = load_etf_kline(code, force=force_klines)
        if df.empty:
            return None
        df = df.dropna(subset=["close_adj"])
        return {
            "code": code,
            "adv20": _adv(df, 20),
            "adv60": _adv(df, 60),
            "history_days": int(len(df)),
            "last_date": df["date"].max(),
            "last_close": float(df["close_adj"].iloc[-1]),
            "first_date": df["date"].min(),
        }

    done = 0
    for c in codes:
        try:
            r = _one(c)
            if r:
                meta_rows.append(r)
        except Exception:  # noqa: BLE001
            pass
        done += 1
        if done % 25 == 0:
            print(f"  [klines] {done}/{len(codes)}", flush=True)
        time.sleep(0.05)

    meta = pd.DataFrame(meta_rows)
    broad = broad.merge(meta, on="code", how="inner")
    # 自动剔除长期无新数据的 ETF（退市/长期停牌），避免它们进入代表池
    if not broad.empty and "last_date" in broad.columns:
        cutoff = broad["last_date"].max() - pd.Timedelta(days=10)
        broad = broad[broad["last_date"] >= cutoff].copy()
    broad["index_key"] = broad.apply(
        lambda r: canonical_index(r["track_index"], r["name"]), axis=1)
    broad["total_fee"] = broad["mgmt_fee"].fillna(0.005) + broad["custody_fee"].fillna(0.001)
    if save:
        save_csv(broad, "broad_meta.csv")
    return broad


def select_representatives(meta: pd.DataFrame) -> pd.DataFrame:
    """按跟踪指数去重，每类指数选流动性最好的代表（避免把同指数多只 ETF 当独立机会）。"""
    meta = meta.sort_values(["adv20", "history_days"], ascending=False)
    rep = meta.drop_duplicates(subset="index_key", keep="first").copy()
    return rep.sort_values(["broad_subtype", "index_key"]).reset_index(drop=True)


def load_broad_meta() -> pd.DataFrame:
    df = load_csv("broad_meta.csv")
    return df if df is not None else build_broad_meta()
