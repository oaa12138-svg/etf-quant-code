"""候选池构建：自动发现 -> 分类 -> 硬性门槛 -> 软性评分。

历史回测时按“上市/成立日期”重建时点池（point-in-time），并使用前复权日线首日作为
实际可交易起点，避免用指数历史伪装成 ETF 真实交易记录。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from ..config import CACHE_DIR, UNIVERSE
from .classify_rules import classify
from .eastmoney import fetch_etf_candidates, fetch_fund_profile, _pct_to_float


# 仅需进一步核实跟踪标的的基金类型（债券/货币/海外在进入前即被排除）
PROFILE_TYPES = ("指数型-股票", "指数型-其他", "股票型")
PROFILE_CACHE = CACHE_DIR / "fund_profiles.csv"


def _load_profiles() -> pd.DataFrame:
    if PROFILE_CACHE.exists():
        return pd.read_csv(PROFILE_CACHE, dtype={"code": str})
    return pd.DataFrame(columns=["code", "track_index", "benchmark", "mgmt_fee",
                                 "custody_fee", "est_date"])


def _fetch_profiles(codes: list[str]) -> pd.DataFrame:
    """抓取缺失的基金档案并落盘缓存。"""
    existing = _load_profiles()
    have = set(existing["code"].astype(str))
    todo = [c for c in codes if c not in have]

    def _one(code):
        try:
            prof = fetch_fund_profile(code)
        except Exception:  # noqa: BLE001
            prof = {"code": code}
        return {
            "code": code,
            "track_index": prof.get("track_index"),
            "benchmark": prof.get("benchmark"),
            "mgmt_fee": _pct_to_float(prof.get("mgmt_fee")),
            "custody_fee": _pct_to_float(prof.get("custody_fee")),
            "est_date": prof.get("est_date"),
        }

    rows = []
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_one, c): c for c in todo}
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception:  # noqa: BLE001
                pass
            done += 1
            if done % 200 == 0:
                print(f"  [profiles] {done}/{len(todo)}")
    if rows:
        new = pd.DataFrame(rows)
        merged = pd.concat([existing, new], ignore_index=True).drop_duplicates(
            subset="code", keep="last")
        merged.to_csv(PROFILE_CACHE, index=False, encoding="utf-8-sig")
        return merged
    return existing


def discover_universe(refresh: bool = False) -> pd.DataFrame:
    """抓取 ETF 列表 + 基金档案并分类，落盘 CSV。"""
    cache = CACHE_DIR / "universe.csv"
    if cache.exists() and not refresh:
        return pd.read_csv(cache, dtype={"code": str})

    cand = fetch_etf_candidates()
    cand = cand[cand["fund_type"].isin(PROFILE_TYPES)].copy()
    prof = _fetch_profiles(list(cand["code"].astype(str)))
    prof = prof[prof["code"].isin(cand["code"].astype(str))]

    merged = cand.merge(prof, on="code", how="left")
    rows = []
    for _, s in merged.iterrows():
        c = classify(s.get("track_index"), s.get("benchmark"), s.get("name", ""))
        rows.append({
            "code": str(s["code"]),
            "name": s.get("name", ""),
            "market": s.get("market"),
            "secid": s.get("secid"),
            "track_index": s.get("track_index"),
            "benchmark": s.get("benchmark"),
            "mgmt_fee": s.get("mgmt_fee"),
            "custody_fee": s.get("custody_fee"),
            "est_date": s.get("est_date"),
            "category": c["category"],
            "category_basis": c["basis"],
            "broad_subtype": c["subtype"],
            "fund_type": s.get("fund_type"),
        })
    df = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    df.to_csv(cache, index=False, encoding="utf-8-sig")
    return df


def load_universe() -> pd.DataFrame:
    return pd.read_csv(CACHE_DIR / "universe.csv", dtype={"code": str})


def hard_filter(univ: pd.DataFrame, kline_meta: pd.DataFrame) -> pd.DataFrame:
    """硬性准入：流动性与可交易性门槛。kline_meta 含 adv20/adv60/history_days。"""
    df = univ.merge(kline_meta, on="code", how="inner")
    df = df[
        (df["adv20"] >= UNIVERSE["min_adv20"])
        & (df["adv60"] >= UNIVERSE["min_adv60"])
        & (df["history_days"] >= UNIVERSE["min_history_days"])
        & (df["last_close"] >= UNIVERSE["min_price"])
    ]
    mgmt = df["mgmt_fee"].fillna(0) + df["custody_fee"].fillna(0)
    df = df[mgmt <= UNIVERSE["max_mgmt_fee"]]
    return df.reset_index(drop=True)
