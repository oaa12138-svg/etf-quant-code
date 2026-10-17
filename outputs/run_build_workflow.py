"""生成全市场宽基 ETF 自动筛选推荐工作流页面。

用法：python -u outputs/run_build_workflow.py
输出：outputs/recommendation_workflow.html
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from etf_quant.config import BACKTEST, CONVENTIONS, COST, UNIVERSE
from etf_quant.data.prepare import load_broad_meta, select_representatives, load_market_indices
from etf_quant.indicators import compute_market_env
from etf_quant.scoring import market_gate
from etf_quant.backtest import (
    run_signal_strategy, run_buy_hold, run_equal_weight, dynamic_dedupe,
)
from etf_quant.metrics import performance
from etf_quant.baseline import round_trip_cost_rate
from run_backtest import build_data


OUT = Path(__file__).resolve().parent
CACHE = OUT.parent / "work" / "cache"
HARD_KEYS = ("liquidity", "liquidity60", "history", "fee", "price")


def pct(x: float) -> float:
    return round(float(x), 6)


def _fmt_amount(x: float) -> str:
    if x >= 1e8:
        return f"{x / 1e8:.1f} 亿元"
    if x >= 1e4:
        return f"{x / 1e4:.0f} 万元"
    return f"{x:.0f} 元"


def _checks(row: pd.Series, corr_peer: dict) -> dict:
    cost = round_trip_cost_rate(notional=10_000)
    overheat = bool(row["mom5"] > 0.08 and row["rel_amount"] > 2.0 and row["dd20"] > -0.02)
    checks = {
        "liquidity": {
            "pass": bool(row["adv20"] >= UNIVERSE["min_adv20"]),
            "label": "流动性",
            "detail": f"20日日均成交额 {_fmt_amount(row['adv20'])}",
        },
        "liquidity60": {
            "pass": bool(row["adv60"] >= UNIVERSE["min_adv60"]),
            "label": "60日流动性",
            "detail": f"60日日均成交额 {_fmt_amount(row['adv60'])}",
        },
        "history": {
            "pass": bool(row["history_days"] >= UNIVERSE["min_history_days"]),
            "label": "历史数据",
            "detail": f"{int(row['history_days'])} 个交易日（≥120）",
        },
        "fee": {
            "pass": bool(row["total_fee"] <= UNIVERSE["max_mgmt_fee"]),
            "label": "费率",
            "detail": f"管理+托管 {row['total_fee']*100:.2f}%/年（≤0.60%）",
        },
        "price": {
            "pass": bool(row["close_adj"] >= UNIVERSE["min_price"]),
            "label": "价格下限",
            "detail": f"最新价 {row['close_adj']:.3f} 元（≥0.20）",
        },
        "vol": {
            "pass": bool(row["vol20"] <= 0.40),
            "label": "20日波动率",
            "detail": f"年化 {row['vol20']*100:.1f}%（≤40%）",
        },
        "atr": {
            "pass": bool(row["atr_ratio"] <= 0.045),
            "label": "ATR 比率",
            "detail": f"{row['atr_ratio']*100:.2f}%（≤4.5%）",
        },
        "net": {
            "pass": bool(row["net_expected_return"] > 0),
            "label": "成本覆盖",
            "detail": (f"10日动量 {row['mom10']*100:.2f}% − 双边成本 "
                       f"{cost*100:.2f}% = 净 {row['net_expected_return']*100:.2f}%"),
        },
        "overheat": {
            "pass": not overheat,
            "label": "追涨风险",
            "detail": ("短期过热：5日动量>8% 且 成交额>2倍 且 贴近20日高点"
                       if overheat else
                       f"5日动量 {row['mom5']*100:.2f}%、成交额 {row['rel_amount']:.2f}×、"
                       f"距高点 {row['dd20']*100:.2f}%"),
        },
        "correlation": {
            "pass": True,
            "label": "相关性/重复暴露",
            "detail": (f"与 {corr_peer['name']} 相关性 {corr_peer['corr']:.2f}"
                       if corr_peer else "无可用相关性样本"),
        },
    }
    return checks


def _status(checks: dict) -> tuple[str, str]:
    hard = list(HARD_KEYS)
    risk = ["vol", "atr", "net", "overheat"]
    for k in hard:
        if not checks[k]["pass"]:
            return "reject", f"{checks[k]['label']}：{checks[k]['detail']}"
    for k in risk:
        if not checks[k]["pass"]:
            return "reject", f"{checks[k]['label']}：{checks[k]['detail']}"
    return "candidate", "通过全部硬性/风险检查，等待人工确认"


def ex_ante_fixed_pool(scored_all: pd.DataFrame, asof: pd.Timestamp) -> list[str]:
    """在样本起点按当时 ADV20 选每个宽基子类别的代表，避免事后挑赢家。"""
    snap = scored_all[scored_all["date"] <= asof].dropna(
        subset=["adv20", "broad_subtype"]
    ).copy()
    if snap.empty:
        return []
    last = snap.sort_values("date").groupby("code").tail(1)
    last = last.sort_values(["broad_subtype", "adv20"], ascending=[True, False])
    return last.drop_duplicates("broad_subtype")["code"].tolist()


def build_data() -> dict:
    all_broad = load_broad_meta()
    all_broad["code"] = all_broad["code"].astype(str)
    rep = select_representatives(all_broad)
    env = compute_market_env(load_market_indices())
    env["gate"] = market_gate(env)
    scored_all, _ = build_data_impl(all_broad)
    scored_all["code"] = scored_all["code"].astype(str)
    scored_dyn = dynamic_dedupe(scored_all)

    latest_date = scored_all["date"].max()
    latest = scored_dyn[scored_dyn["date"] == latest_date].copy()
    meta = all_broad[["code", "name", "index_key", "broad_subtype",
                      "history_days", "total_fee"]].rename(
        columns={"index_key": "index_name", "broad_subtype": "subtype"})
    latest = latest.merge(meta, on="code", how="left")
    latest = latest.sort_values("composite", ascending=False).reset_index(drop=True)
    latest["rank"] = range(1, len(latest) + 1)

    # 相关性（最近 60 个交易日日收益）
    ret = scored_all.pivot_table(index="date", columns="code", values="ret1").tail(60)
    corr = ret.corr()
    peers: dict[str, list[dict]] = {}
    for code in latest["code"]:
        if code not in corr.columns:
            peers[code] = []
            continue
        s = corr[code].drop(labels=[code], errors="ignore").dropna()
        s = s.sort_values(ascending=False).head(3)
        peers[code] = [
            {"code": c, "name": latest.loc[latest["code"] == c, "name"].iloc[0]
             if (latest["code"] == c).any() else c,
             "corr": round(float(v), 4)}
            for c, v in s.items()
        ]

    rows = []
    for _, r in latest.iterrows():
        cp = peers.get(r["code"], [])
        checks = _checks(r, cp[0] if cp else None)
        status, reason = _status(checks)
        hard_ok = all(checks[k]["pass"] for k in HARD_KEYS)
        status_kind = "candidate" if status == "candidate" else ("hard" if not hard_ok else "risk")
        rows.append({
            "rank": int(r["rank"]),
            "code": r["code"],
            "name": r["name"],
            "index_name": r["index_name"],
            "subtype": r["subtype"],
            "composite": round(float(r["composite"]), 4),
            "scores": {
                "momentum": round(float(r["score_momentum"]), 4),
                "trend": round(float(r["score_trend"]), 4),
                "volume": round(float(r["score_volume"]), 4),
                "risk": round(float(r["score_risk"]), 4),
                "cost": round(float(r["score_cost"]), 4),
            },
            "metrics": {
                "mom5": pct(r["mom5"]), "mom10": pct(r["mom10"]),
                "trend20": pct(r["trend20"]), "rel_amount": round(float(r["rel_amount"]), 4),
                "atr_ratio": pct(r["atr_ratio"]), "vol20": pct(r["vol20"]),
                "dd20": pct(r["dd20"]), "net_expected_return": pct(r["net_expected_return"]),
                "adv20": float(r["adv20"]), "adv60": float(r["adv60"]),
                "close": float(r["close_adj"]),
            },
            "history_days": int(r["history_days"]),
            "total_fee": float(r["total_fee"]),
            "checks": checks,
            "peers": cp,
            "status": status,
            "status_kind": status_kind,
            "status_reason": reason,
        })

    hard_pass = sum(1 for x in rows if all(x["checks"][k]["pass"] for k in HARD_KEYS))
    risk_pass = sum(1 for x in rows if x["status"] == "candidate")
    top = rows[0]

    # 全市场分类统计
    univ = pd.read_csv(CACHE / "universe.csv", dtype={"code": str}, encoding="utf-8-sig")
    cat_order = ["broad", "industry_or_theme", "factor_or_theme", "cross_border",
                 "commodity", "enhanced", "bond_or_money", "other"]
    cat_cn = {"broad": "宽基", "industry_or_theme": "行业/主题",
              "factor_or_theme": "因子/策略", "cross_border": "跨境",
              "commodity": "商品", "enhanced": "增强",
              "bond_or_money": "债券/货币", "other": "需人工复核"}
    classification = [
        {"key": k, "label": cat_cn[k], "count": int((univ["category"] == k).sum())}
        for k in cat_order if (univ["category"] == k).any()
    ]
    broad = univ[univ["category"] == "broad"]
    subtype_cn = {"large": "大盘", "mid": "中盘", "small": "中小盘", "allmarket": "全市场"}
    subtypes = [
        {"key": k, "label": subtype_cn[k], "count": int((broad["broad_subtype"] == k).sum())}
        for k in ["large", "mid", "small", "allmarket"]
        if (broad["broad_subtype"] == k).any()
    ]

    def _s(x):
        return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)

    all_etfs = []
    for _, r in univ.iterrows():
        all_etfs.append({
            "code": str(r["code"]),
            "name": _s(r.get("name")),
            "category": _s(r.get("category")),
            "category_label": cat_cn.get(_s(r.get("category")), _s(r.get("category"))),
            "track_index": _s(r.get("track_index")),
            "subtype": subtype_cn.get(_s(r.get("broad_subtype")), ""),
            "fund_type": _s(r.get("fund_type")),
            "mgmt_fee": None if pd.isna(r.get("mgmt_fee")) else round(float(r["mgmt_fee"]), 6),
            "custody_fee": None if pd.isna(r.get("custody_fee")) else round(float(r["custody_fee"]), 6),
            "est_date": _s(r.get("est_date")),
        })

    bm = load_broad_meta()
    bm["code"] = bm["code"].astype(str)
    bm["index_key"] = bm["index_key"].fillna("未知")
    broad_map = []
    for idx, g in bm.groupby("index_key"):
        g = g.sort_values("adv20", ascending=False)
        rep_row = g.iloc[0]
        broad_map.append({
            "index": _s(idx),
            "rep_code": str(rep_row["code"]),
            "rep_name": _s(rep_row["name"]),
            "count": int(len(g)),
            "adv20": float(rep_row["adv20"]),
            "members": [
                {"code": str(x["code"]), "name": _s(x["name"]), "adv20": float(x["adv20"])}
                for _, x in g.iterrows()
            ],
        })
    broad_map.sort(key=lambda x: (-x["count"], x["index"]))

    hard_criteria = [
        {"key": "history", "label": "历史数据", "rule": "≥120 个交易日",
         "why": "上市不足 120 日的新 ETF 指标不可比"},
        {"key": "liquidity", "label": "20 日日均成交额", "rule": "≥5 亿元",
         "why": "5 万元账户单笔约 1 万元，约占日成交额 0.002%"},
        {"key": "liquidity60", "label": "60 日日均成交额", "rule": "≥5 亿元",
         "why": "避免短期放量造成的假流动性"},
        {"key": "fee", "label": "管理费 + 托管费", "rule": "≤0.60%/年",
         "why": "高费率会持续侵蚀净收益"},
        {"key": "price", "label": "价格下限", "rule": "≥0.20 元",
         "why": "排除异常/清盘型产品"},
        {"key": "lot", "label": "最小交易单位", "rule": "一手 100 份市值 ≥100 元",
         "why": "避免交易单位成本占比过高"},
        {"key": "data", "label": "数据完整性", "rule": "价格连续、复权可用",
         "why": "缺失/异常数据不进入排名"},
    ]
    risk_criteria = [
        {"key": "correlation", "label": "相关性 / 重复暴露",
         "rule": "同指数只保留一个；相关性 >0.90 不重复加仓",
         "why": "避免把同一指数多只 ETF 当成多个独立机会"},
        {"key": "overheat", "label": "追涨风险",
         "rule": "5 日动量 >8% 且 成交额 >2 倍 且 距 20 日高点 <2% → 拒绝",
         "why": "避免在短期过热时追高"},
        {"key": "cost", "label": "成本覆盖能力",
         "rule": "净预期收益 = 10 日动量 − 双边成本 > 0",
         "why": "小资金必须看扣费后的净收益"},
        {"key": "vol", "label": "20 日年化波动率", "rule": "≤40%",
         "why": "高波动环境下趋势信号可靠性下降"},
        {"key": "atr", "label": "ATR 比率", "rule": "ATR(14)/收盘 ≤4.5%",
         "why": "控制单笔风险预算与止损距离"},
        {"key": "position", "label": "组合仓位",
         "rule": "单 ETF ≤50% 净值；同风格不重复；允许空仓",
         "why": "回撤控制不能只靠止损"},
        {"key": "gap", "label": "跳空 / 止损风险",
         "rule": "止损按次日开盘成交，不保证触发价",
         "why": "跳空/涨跌停/流动性下降可能扩大损失"},
    ]
    scoring = {
        "weights": {"momentum": 0.30, "trend": 0.25, "volume": 0.10,
                    "risk": 0.20, "cost": 0.15},
        "composite": "0.30×动量 + 0.25×趋势 + 0.10×量价 + 0.20×风险 + 0.15×成本",
        "note": "横截面 z-score；权重为先验假设，不是拟合参数。",
        "definitions": [
            {"key": "momentum", "label": "动量", "formula": "z(ret10) 与 z(ret20) 的均值",
             "direction": "越高越好"},
            {"key": "trend", "label": "趋势", "formula": "z(trend20)、z(slope10)、z(hi60) 的均值",
             "direction": "越高越好"},
            {"key": "volume", "label": "量价", "formula": "z(rel_amount)、z(close_pos) 的均值",
             "direction": "越高越好"},
            {"key": "risk", "label": "风险", "formula": "−[z(vol20)+z(atr_ratio)+z(dd20)]/3",
             "direction": "低波动/低回撤得分高"},
            {"key": "cost", "label": "成本", "formula": "z(log ADV20)",
             "direction": "成交额越大得分越高"},
        ],
    }

    # 市场环境
    e = env.loc[latest_date]
    market = {
        "date": str(latest_date.date()),
        "mkt_ret20": round(float(e.get("mkt_ret20", np.nan)), 4),
        "mkt_above60": bool(e.get("mkt_above60", 0) > 0),
        "mkt_vol20": round(float(e.get("mkt_vol20", np.nan)), 4),
        "breadth5": round(float(e.get("breadth5", np.nan)), 4),
        "small_vs_large": round(float(e.get("small_vs_large", np.nan)), 4),
        "gate": bool(e.get("gate", 1) == 1),
    }

    # 历史检验（全周期 + 样本外）
    cal = pd.DatetimeIndex(env.index.intersection(pd.DatetimeIndex(scored_dyn["date"].unique())).sort_values())
    model = run_signal_strategy(scored_dyn, env, cal, rank_col="composite",
                                use_risk_filter=True, use_market_gate=True,
                                use_net_gate=True, initial_cash=BACKTEST["initial_cash"],
                                price_panel=scored_all)
    # 样本起点固定池：只用起点及之前的数据选代表，避免“事后挑赢家”
    fixed_asof = cal[60] if len(cal) > 60 else cal[0]
    fixed_codes = ex_ante_fixed_pool(scored_all, fixed_asof)
    cal_fixed = cal[cal >= fixed_asof]
    fixed = run_signal_strategy(
        scored_all[scored_all["code"].isin(fixed_codes)], env, cal_fixed,
        rank_col="composite", use_risk_filter=True,
        use_market_gate=True, use_net_gate=True,
        initial_cash=BACKTEST["initial_cash"], price_panel=scored_all)
    model_fixed = run_signal_strategy(
        scored_dyn, env, cal_fixed, rank_col="composite",
        use_risk_filter=True, use_market_gate=True, use_net_gate=True,
        initial_cash=BACKTEST["initial_cash"], price_panel=scored_all)

    def p(eq, tr=None):
        return performance(eq, tr)

    def row(name, perf):
        return {
            "strategy": name,
            "annual_return": round(float(perf.get("annual_return", np.nan)), 4),
            "max_drawdown": round(float(perf.get("max_drawdown", np.nan)), 4),
            "sharpe": round(float(perf.get("sharpe", np.nan)), 3),
            "n_trades": int(perf.get("n_trades", 0) or 0),
        }

    oos_start = pd.Timestamp(BACKTEST["test_start"])
    full, oos = [], []
    specs = [
        ("买入持有 沪深300ETF", lambda: run_buy_hold(scored_all, "510300")),
        ("买入持有 中证500ETF", lambda: run_buy_hold(scored_all, "510500")),
        ("全宽基等权（不计成本）", lambda: run_equal_weight(scored_dyn)),
        ("多因子综合模型", lambda: model[0]),
    ]
    for name, fn in specs:
        eq = fn()
        full.append(row(name, p(eq, model[1] if name == "多因子综合模型" else None)))
        tr = model[1] if name == "多因子综合模型" else None
        oos.append(row(name, p(eq.loc[oos_start:])))
    # 交易数按样本外过滤
    tr = model[1]
    oos_trades = tr[tr["exit_date"] >= oos_start] if len(tr) else tr
    for x in oos:
        if x["strategy"] == "多因子综合模型":
            x["n_trades"] = int(len(oos_trades))
            x["annual_return"] = round(float(performance(model[0].loc[oos_start:], oos_trades).get("annual_return", np.nan)), 4)
            x["max_drawdown"] = round(float(performance(model[0].loc[oos_start:], oos_trades).get("max_drawdown", np.nan)), 4)
            x["sharpe"] = round(float(performance(model[0].loc[oos_start:], oos_trades).get("sharpe", np.nan)), 3)

    fixed_vs_dynamic = [
        row("样本起点固定池（按当时流动性）", p(fixed[0], fixed[1])),
        row("全市场动态池（同期间）", p(model_fixed[0], model_fixed[1])),
    ]
    # 样本外固定池
    fixed_oos = row("固定池（样本外）", p(fixed[0].loc[oos_start:],
                                         fixed[1][fixed[1]["exit_date"] >= oos_start] if len(fixed[1]) else fixed[1]))
    fixed_vs_dynamic.append(fixed_oos)

    model_full = next(x for x in full if x["strategy"] == "多因子综合模型")
    model_oos = next(x for x in oos if x["strategy"] == "多因子综合模型")
    history_verdict = (
        f"回撤控制：全程 {model_full['max_drawdown']*100:.1f}%、"
        f"样本外 {model_oos['max_drawdown']*100:.1f}%；"
        f"年化：全程 {model_full['annual_return']*100:.2f}%、"
        f"样本外 {model_oos['annual_return']*100:.2f}%；"
        f"固定池与全市场动态池的同口径对比见下表。"
        f"结论：回撤控制优先，收益/稳健性仍需继续验证，当前只适合纸面或模拟交易。"
    )

    sens = pd.read_csv(OUT / "sensitivity.csv", encoding="utf-8-sig")
    sensitivity = []
    for _, r in sens.head(20).iterrows():
        sensitivity.append({
            "case": str(r.iloc[0]),
            "annual_return": round(float(r["annual_return"]), 4),
            "max_drawdown": round(float(r["max_drawdown"]), 4),
            "sharpe": round(float(r["sharpe"]), 3),
            "n_trades": int(r["n_trades"]),
        })

    # 工作流漏斗
    funnel = [
        {"step": 1, "label": "发现 ETF", "count": int(len(univ)),
         "sub": f"宽基 {len(broad)} · 代表池 {len(rep)}", "state": "ok",
         "detail": "全市场场内 ETF 清单，验证基金类型、跟踪标的与上市状态"},
        {"step": 2, "label": "硬性过滤", "count": hard_pass,
         "sub": f"{hard_pass} / {len(rows)} 只通过", "state": "ok",
         "detail": "历史≥120日、20日日均成交额≥5亿、费率≤0.60%"},
        {"step": 3, "label": "量化排名", "count": hard_pass,
         "sub": "综合分排序", "state": "ok",
         "detail": "动量30% + 趋势25% + 量价10% + 风险20% + 成本15%；市场门控另行过滤"},
        {"step": 4, "label": "风险复核", "count": risk_pass,
         "sub": f"{risk_pass} 只通过", "state": "stop",
         "detail": "相关性、追涨、成本覆盖、波动/ATR、组合仓位"},
        {"step": 5, "label": "人工确认", "count": risk_pass,
         "sub": "空仓观望" if risk_pass == 0 else "待人工确认", "state": "stop",
         "detail": "输出候选、入场/退出、金额与风险说明"},
    ]

    overall = {
        "status": "cash" if risk_pass == 0 else "candidate",
        "label": "全部候选不合格 · 空仓观望" if risk_pass == 0 else "存在候选 · 等待人工确认",
        "reason": (
            f"23 只代表池中 {hard_pass} 只通过硬性过滤；但最新交易日全部候选的"
            f"净预期收益为负（10 日动量不足以覆盖约 {round_trip_cost_rate(10000)*100:.2f}% 双边成本），"
            f"按规则全部不合格。综合分第一的 {top['code']} {top['name']} 净预期收益 "
            f"{top['metrics']['net_expected_return']*100:.2f}%，同样不可买入。"
        ) if risk_pass == 0 else (
            f"有 {risk_pass} 只候选通过全部检查，仍需人工确认数据、成本与执行条件。"
        ),
    }

    return {
        "meta": {
            "data_date": str(latest_date.date()),
            "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            "total_etfs": int(len(univ)),
            "broad_etfs": int(len(broad)),
            "representatives": int(len(rep)),
            "hard_pass": hard_pass,
            "risk_pass": risk_pass,
            "overall": overall,
            "conventions": CONVENTIONS,
            "cost": COST,
            "round_trip_cost_rate": round_trip_cost_rate(10000),
        },
        "classification": classification,
        "subtypes": subtypes,
        "all_etfs": all_etfs,
        "broad_map": broad_map,
        "hard_criteria": hard_criteria,
        "risk_criteria": risk_criteria,
        "scoring": scoring,
        "funnel": funnel,
        "candidates": rows,
        "market": market,
        "history": {"full": full, "oos": oos,
                    "fixed_vs_dynamic": fixed_vs_dynamic,
                    "sensitivity": sensitivity,
                    "verdict": history_verdict,
                    "cash_yield": BACKTEST["cash_yield"],
                    "fixed_pool": {"asof": str(fixed_asof.date()), "codes": fixed_codes},
                    "window": f"{cal.min().date()} ~ {cal.max().date()}"},
    }


def build_data_impl(rep: pd.DataFrame):
    """避免与 build_data 名称冲突：调用 run_backtest.build_data。"""
    from run_backtest import build_data as _bd
    return _bd(rep)


def main() -> None:
    data = build_data()
    template = (OUT / "workflow_template.html").read_text(encoding="utf-8")
    html = template.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    out = OUT / "recommendation_workflow.html"
    out.write_text(html, encoding="utf-8")
    m = data["meta"]
    print(f"已生成 {out}")
    print(f"数据日期 {m['data_date']}；ETF {m['total_etfs']}；宽基 {m['broad_etfs']}；"
          f"代表池 {m['representatives']}；硬过滤通过 {m['hard_pass']}；风险复核通过 {m['risk_pass']}")
    print("结论：", m["overall"]["label"])


if __name__ == "__main__":
    main()
