"""步骤 2：构建数据集（宽基 ETF 日线 + 市场指数），生成元信息与代表池。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from etf_quant.data.prepare import build_broad_meta, select_representatives, load_market_indices


def main() -> None:
    print("抓取宽基 ETF 日线并计算流动性元信息 ...", flush=True)
    meta = build_broad_meta(refresh=False)
    print(f"宽基 ETF 元信息：{len(meta)} 只", flush=True)

    rep = select_representatives(meta)
    print(f"按跟踪指数去重后的代表池：{len(rep)} 只", flush=True)
    cols = ["code", "name", "index_key", "broad_subtype", "adv20",
            "history_days", "total_fee", "last_date"]
    print(rep[cols].to_string(index=False), flush=True)

    idx = load_market_indices()
    print("市场指数覆盖：", flush=True)
    for k, v in idx.items():
        print(f"  {k}: {len(v)} 根（{v['date'].min().date()} ~ {v['date'].max().date()}）", flush=True)

    rep.to_csv(Path(__file__).resolve().parent / "etf_representatives.csv",
               index=False, encoding="utf-8-sig")
    print("已保存 etf_representatives.csv", flush=True)


if __name__ == "__main__":
    main()
