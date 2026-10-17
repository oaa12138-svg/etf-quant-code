"""步骤 1：自动发现全市场场内 ETF 并分类。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from etf_quant.data.universe import discover_universe  # noqa: E402


def main() -> None:
    print("发现全市场场内 ETF ...")
    df = discover_universe(refresh=False)
    print(f"共发现 {len(df)} 只 ETF。")
    print("\n分类统计：")
    print(df["category"].value_counts().to_string())

    broad = df[df["category"] == "broad"].sort_values(["broad_subtype", "code"])
    out = Path(__file__).resolve().parent / "etf_broad_universe.csv"
    broad.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n宽基 ETF 数量：{len(broad)}，已保存 {out.name}")
    print("\n宽基 ETF 预览（按子类别+成交额排序，前 40）：")
    cols = ["code", "name", "track_index", "broad_subtype"]
    print(broad[cols].head(40).to_string(index=False))

    other = df[df["category"] == "other"]
    if not other.empty:
        print(f"\n需人工复核（other）数量：{len(other)}")
        print(other[["code", "name", "track_index"]].head(50).to_string(index=False))


if __name__ == "__main__":
    main()
