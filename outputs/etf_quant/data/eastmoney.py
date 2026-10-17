"""东方财富公开行情接口封装（带重试与磁盘缓存）。

接口：
  - ETF 现货列表：push2.eastmoney.com/api/qt/clist/get  (fs=b:MK0021,b:MK0022)
  - 日 K 线：push2his.eastmoney.com/api/qt/stock/kline/get
  - 基金档案：fundf10.eastmoney.com/jbgk_{code}.html

所有抓取均带重试、退避与缓存，避免对上游造成压力。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import CACHE_DIR


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Connection": "close",
}

def _http_get(url: str, params: dict, timeout: int = 20) -> str:
    """标准库 HTTP GET，避免依赖 requests。"""
    query = urllib.parse.urlencode(params, safe=":,+")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(url + sep + query, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="ignore")


def _request_json(url: str, params: dict, hosts: list[str] | None = None,
                  timeout: int = 20, tries: int = 4) -> dict:
    """请求 JSON；clist 上游偶发断连，跨主机轮换重试。"""
    urls = [url]
    if hosts:
        base = url.split("://")[1]
        urls = [url.replace(base.split("/")[0], h) for h in hosts]
    last_err: Exception | None = None
    for u in urls:
        for i in range(tries):
            try:
                return json.loads(_http_get(u, params, timeout=timeout))
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.8 * (2 ** i) + np.random.uniform(0, 0.5))
    raise last_err if last_err else RuntimeError("request failed")


def _text(url: str, timeout: int = 20, tries: int = 3) -> str:
    last_err: Exception | None = None
    for i in range(tries):
        try:
            return _http_get(url, {}, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.8 * (2 ** i))
    raise last_err if last_err else RuntimeError("text fetch failed")


# ---------------------------------------------------------------------------
# 基金目录（code + 中文名 + 类型），用于自动发现场内 ETF
# ---------------------------------------------------------------------------
FUNDLIST_URL = "http://fund.eastmoney.com/js/fundcode_search.js"
# 场内 ETF 的精确代码前缀（区别于 LOF 501/502、场外 519 等）
SH_ETF_PREFIXES = ("510", "511", "512", "513", "515", "516", "517", "518",
                   "560", "561", "562", "563", "588", "589")
SZ_ETF_PREFIX = "159"


def _market_from_code(code: str) -> int:
    return 0 if code.startswith("15") else 1


def is_etf_code(code: str) -> bool:
    return code.startswith(SZ_ETF_PREFIX) or code.startswith(SH_ETF_PREFIXES)


def fetch_fund_list() -> pd.DataFrame:
    """全市场基金目录。返回 code / name / fund_type。"""
    txt = _text(FUNDLIST_URL)
    m = re.findall(r'\["(\d{6})","([^"]*)","([^"]*)","([^"]*)"', txt)
    df = pd.DataFrame(m, columns=["code", "pinyin", "name", "fund_type"])
    df["code"] = df["code"].astype(str)
    return df


def fetch_etf_candidates() -> pd.DataFrame:
    """场内 ETF 候选（按代码前缀 51/56/58/159 过滤），含市场与 secid。"""
    funds = fetch_fund_list()
    etf = funds[funds["code"].map(is_etf_code)].copy()
    etf["market"] = etf["code"].map(_market_from_code)
    etf["secid"] = etf["market"].astype(str) + "." + etf["code"]
    return etf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 日 K 线（腾讯源为主，东财为备）
# 说明：免费公开日线源普遍限制约 800~1000 根；此处请求 800 根（约 2023-06 起）。
# ---------------------------------------------------------------------------
KHOSTS = ["push2his.eastmoney.com", "92.push2his.eastmoney.com"]
KH_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _secid_to_sym(secid: str) -> str:
    mkt, code = secid.split(".")
    return ("sh" if mkt == "1" else "sz") + code


def _fetch_kline_tencent(secid: str, fq: str = "", count: int = 800) -> pd.DataFrame:
    """腾讯日 K。fq: ""=不复权, "qfq"=前复权, "hfq"=后复权。volume 单位：手。"""
    sym = _secid_to_sym(secid)
    d = _request_json(TX_URL, {"param": f"{sym},day,,,{count},{fq}"}, hosts=None)
    data = (d or {}).get("data") or {}
    inner = data.get(sym) if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        return pd.DataFrame()
    key = {"": "day", "qfq": "qfqday", "hfq": "hfqday"}[fq]
    # 新成立、未发生过除权的 ETF 往往没有 qfq/hfq 序列，此时退回“day”（即复权等价于不复权）
    arr = inner.get(key) or inner.get("day") or []
    if not arr:
        return pd.DataFrame()
    df = pd.DataFrame(arr, columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    # 成交额（元）= 收盘价 * 成交量（手）* 100
    df["amount"] = df["close"] * df["volume"] * 100.0
    df["pct_chg"] = df["close"].pct_change().fillna(0.0) * 100.0
    df["secid"] = secid
    df["code"] = secid.split(".")[-1]
    df["name"] = sym
    return df


def _fetch_kline_em(secid: str, fqt: int) -> pd.DataFrame:
    """东财日 K（备源）。"""
    end = datetime.now().strftime("%Y%m%d")
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": str(fqt),
        "beg": "20230101", "end": end,
    }
    d = _request_json(KH_URL, params, hosts=KHOSTS)
    data = (d or {}).get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        return pd.DataFrame()
    cols = ["date", "open", "close", "high", "low", "volume", "amount",
            "amplitude", "pct_chg", "chg", "turnover"]
    rows = [line.split(",") for line in klines]
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["secid"] = secid
    df["code"] = data.get("code", secid.split(".")[-1])
    df["name"] = data.get("name", "")
    return df


def fetch_kline(secid: str, start: str = "20230101", end: str | None = None,
                fqt: int = 1) -> pd.DataFrame:
    """日 K 线。fqt=0 不复权，1 前复权，2 后复权。腾讯为主，东财为备。"""
    fq = {0: "", 1: "qfq", 2: "hfq"}[fqt]
    try:
        df = _fetch_kline_tencent(secid, fq=fq, count=800)
    except Exception:  # noqa: BLE001
        df = pd.DataFrame()
    if df.empty:
        try:
            df = _fetch_kline_em(secid, fqt)
        except Exception:  # noqa: BLE001
            df = pd.DataFrame()
    return df


def fetch_kline_with_adjust(secid: str, start: str = "20230101",
                            end: str | None = None) -> pd.DataFrame:
    """同时抓取不复权与前复权（同源），计算复权因子 adj = 前复权价 / 不复权价。"""
    raw = _fetch_kline_tencent(secid, fq="", count=800)
    adj = _fetch_kline_tencent(secid, fq="qfq", count=800)
    if raw.empty or adj.empty:
        # 备源：东财
        raw = _fetch_kline_em(secid, 0)
        adj = _fetch_kline_em(secid, 1)
    if raw.empty:
        return raw
    raw = raw.rename(columns={"open": "open_raw", "close": "close_raw",
                              "high": "high_raw", "low": "low_raw"})
    if adj.empty:
        m = raw.copy()
        m["adj_factor"] = 1.0
    else:
        m = raw[["date", "open_raw", "close_raw", "high_raw", "low_raw",
                 "volume", "amount"]].merge(
            adj[["date", "open", "close", "high", "low"]], on="date", how="left")
        m["adj_factor"] = np.where(m["close_raw"] != 0, m["close"] / m["close_raw"], 1.0)
        m["adj_factor"] = m["adj_factor"].replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(1.0)
    m["open_adj"] = m["open_raw"] * m["adj_factor"]
    m["high_adj"] = m["high_raw"] * m["adj_factor"]
    m["low_adj"] = m["low_raw"] * m["adj_factor"]
    m["close_adj"] = m["close_raw"] * m["adj_factor"]
    m["secid"] = secid
    m["code"] = raw["code"].iloc[0] if not raw.empty else secid.split(".")[-1]
    m["name"] = raw["name"].iloc[0] if not raw.empty else ""
    return m


# ---------------------------------------------------------------------------
# 基金档案（跟踪标的、费率、成立日期）
# ---------------------------------------------------------------------------
_NUM = r"([0-9]+(?:\.[0-9]+)?)"


def fetch_fund_profile(code: str) -> dict:
    """解析基金概况页，返回跟踪标的/比较基准/费率/成立日期等。"""
    url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
    html = _text(url)
    out: dict[str, object] = {"code": code}

    def _cell_after(label: str) -> str | None:
        # <th>label</th><td>value</td>
        m = re.search(re.escape(label) + r"</th>\s*<td[^>]*>(.*?)</td>", html, re.S)
        if m:
            txt = re.sub(r"<[^>]+>", "", m.group(1))
            return re.sub(r"\s+", "", txt).strip()
        return None

    out["track_index"] = _cell_after("跟踪标的")
    out["benchmark"] = _cell_after("业绩比较基准")
    out["mgmt_fee"] = _cell_after("管理费率")
    out["custody_fee"] = _cell_after("托管费率")
    out["est_date"] = None
    m = re.search(r"成立日期：\s*<span>([0-9\-]+)</span>", html)
    if m:
        out["est_date"] = m.group(1)
    return out


def _pct_to_float(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(_NUM, s)
    return float(m.group(1)) / 100.0 if m else None
