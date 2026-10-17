"""本地 CSV 缓存，避免重复抓取并对上游友好。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import CACHE_DIR


def _path(name: str) -> Path:
    return Path(CACHE_DIR) / name


def save_csv(df: pd.DataFrame, name: str) -> None:
    _path(name).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(_path(name), index=False, encoding="utf-8-sig")


def load_csv(name: str) -> pd.DataFrame | None:
    p = _path(name)
    if not p.exists():
        return None
    df = pd.read_csv(p, encoding="utf-8-sig")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def kline_path(code: str) -> Path:
    return _path(f"kline/{code}.csv")


def save_kline(code: str, df: pd.DataFrame) -> None:
    kline_path(code).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(kline_path(code), index=False, encoding="utf-8-sig")


def load_kline(code: str) -> pd.DataFrame | None:
    p = kline_path(code)
    if not p.exists():
        return None
    df = pd.read_csv(p, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"])
    return df
