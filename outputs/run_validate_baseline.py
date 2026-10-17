"""步骤 0：验证 8 个基础指标的定义、口径与无未来函数。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from etf_quant.baseline import Conventions, compute_baseline, round_trip_cost_rate
from etf_quant.data.prepare import load_etf_kline


OUT = Path(__file__).resolve().parent


def _approx(a: float, b: float, tol: float = 1e-10) -> bool:
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def manual_check(k: pd.DataFrame) -> dict:
    """用显式下标重算最新一日的 8 个指标，和 compute_baseline 对比。"""
    k = k.sort_values("date").reset_index(drop=True)
    c = k["close_adj"].astype(float).to_numpy()
    h = k["high_adj"].astype(float).to_numpy()
    l = k["low_adj"].astype(float).to_numpy()
    amt = k["amount"].astype(float).to_numpy()
    n = len(k)
    i = n - 1

    mom5 = c[i] / c[i - 5] - 1
    mom10 = c[i] / c[i - 10] - 1
    trend20 = c[i] / c[i - 19:i + 1].mean() - 1
    rel_amount = amt[i] / amt[i - 20:i].mean()

    # Wilder ATR 显式重算
    prev = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)
    atr = np.full(n, np.nan)
    atr[13] = tr[:14].mean()  # 首根 TR=H-L，前 14 根 TR 的均值
    for j in range(14, n):
        atr[j] = (atr[j - 1] * 13 + tr[j]) / 14
    atr_ratio = atr[i] / c[i]

    rets = c[1:] / c[:-1] - 1
    vol20 = rets[-20:].std(ddof=1) * np.sqrt(244)
    dd20 = c[i] / c[i - 19:i + 1].max() - 1
    cost = round_trip_cost_rate(notional=10_000)
    net = mom10 - cost

    base = compute_baseline(k).iloc[-1]
    expected = {
        "mom5": mom5, "mom10": mom10, "trend20": trend20,
        "rel_amount": rel_amount, "atr_ratio": atr_ratio,
        "vol20": vol20, "dd20": dd20, "net_expected_return": net,
    }
    report = {}
    for name, val in expected.items():
        got = float(base[name])
        report[name] = (got, val, _approx(got, val))
    return report


def no_lookahead_check(k: pd.DataFrame, dates: list[pd.Timestamp]) -> bool:
    """截断到 t 重算，t 日数值应与全样本在 t 日完全一致。"""
    full = compute_baseline(k)
    cols = ["mom5", "mom10", "trend20", "rel_amount", "atr_ratio", "vol20", "dd20"]
    ok = True
    for d in dates:
        sub = k[k["date"] <= d]
        if len(sub) < 65:
            continue
        part = compute_baseline(sub)
        if d not in part.index:
            continue
        for col in cols:
            a = float(full.loc[d, col])
            b = float(part.loc[d, col])
            if not (np.isnan(a) and np.isnan(b)) and not _approx(a, b, 1e-9):
                print(f"  [未来函数] {d.date()} {col}: full={a:.12g} trunc={b:.12g}")
                ok = False
    return ok


def main() -> None:
    conv = Conventions()
    print("统一口径：", conv)
    print("双边成本率（1 万元名义）：", round(round_trip_cost_rate(10_000) * 10000, 2), "bp")

    codes = ["510300", "510500", "159915", "588000"]
    frames = []
    for code in codes:
        k = load_etf_kline(code)
        if k.empty:
            continue
        base = compute_baseline(k)
        base["code"] = code
        frames.append(base.reset_index())
        if code == "510300":
            print(f"\n510300 最新交易日：{base.index[-1].date()}")
            rep = manual_check(k)
            print(f"{'指标':<20}{'计算值':>14}{'手工值':>14}{'一致':>6}")
            for name, (got, exp, ok) in rep.items():
                print(f"{name:<20}{got:>14.8f}{exp:>14.8f}{str(ok):>6}")
            assert all(v[2] for v in rep.values()), "基础指标与手工重算不一致"

            dates = list(k["date"].sort_values().iloc[65::50])
            assert no_lookahead_check(k, dates), "存在未来函数"
            print("\n无未来函数检查：通过（截断重算与全样本一致）")

            warm = base.notna().sum()
            print("\n有效样本数（共 %d 行）：" % len(base))
            for col in ["mom5", "mom10", "trend20", "rel_amount",
                        "atr_ratio", "vol20", "dd20", "net_expected_return"]:
                print(f"  {col:<20}{int(warm[col]):>5}")

    allb = pd.concat(frames, ignore_index=True)
    latest = allb.groupby("code").tail(30)
    latest.to_csv(OUT / "baseline_sample.csv", index=False, encoding="utf-8-sig")
    print("\n已保存 baseline_sample.csv（最近 30 个交易日 × 4 只代表 ETF）")

    print("\n510300 最近 5 行基础指标：")
    show = latest[latest["code"] == "510300"][
        ["date", "mom5", "mom10", "trend20", "rel_amount",
         "atr_ratio", "vol20", "dd20", "net_expected_return"]].tail(5)
    print(show.to_string(index=False, float_format=lambda x: f"{x:,.6f}"))


if __name__ == "__main__":
    main()
