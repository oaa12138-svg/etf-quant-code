"""每日更新：刷新 ETF 清单/日线 -> 重算敏感性 -> 重建推荐工作流页面。

用法（收盘后）：
    python -u outputs/run_daily_update.py

可选：
    --skip-universe      跳过基金目录/档案刷新（适合每周跑一次）
    --skip-sensitivity   跳过参数敏感性重算
    --skip-page          只刷新数据，不重建页面
    --fast               只强制刷新少量 ETF，用于网络连通性测试
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from etf_quant.data.prepare import (
    build_broad_meta, load_market_indices, select_representatives,
)
from etf_quant.data.universe import discover_universe


OUT = Path(__file__).resolve().parent


def _run(script: str) -> None:
    print(f">>> {script}", flush=True)
    subprocess.run([sys.executable, "-u", str(OUT / script)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-universe", action="store_true")
    ap.add_argument("--skip-sensitivity", action="store_true")
    ap.add_argument("--skip-page", action="store_true")
    ap.add_argument("--skip-github", action="store_true")
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    errors: list[str] = []
    status = "ok"
    github_status = "skipped"

    univ = None
    if not args.skip_universe:
        try:
            univ = discover_universe(refresh=True)
            print(f"[1/4] 基金目录/分类：{len(univ)} 只 ETF", flush=True)
        except Exception as e:  # noqa: BLE001
            errors.append(f"universe: {e}")
            status = "partial"
            print(f"[1/4] 基金目录刷新失败，沿用旧缓存：{e}", flush=True)

    meta = None
    try:
        meta = build_broad_meta(
            force_klines=True,
            max_codes=(12 if args.fast else None),
            save=not args.fast,
        )
        print(f"[2/4] 宽基日线：{len(meta)} 只（{'快速测试' if args.fast else '全量'}）", flush=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"klines: {e}")
        status = "partial"
        print(f"[2/4] 日线刷新失败：{e}", flush=True)

    latest = None
    try:
        idx = load_market_indices(force=True)
        latest = max(df["date"].max() for df in idx.values())
        print(f"[3/4] 市场指数：{len(idx)} 个，最新 {latest.date()}", flush=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"indices: {e}")
        status = "partial"
        print(f"[3/4] 指数刷新失败：{e}", flush=True)

    if not args.skip_sensitivity:
        try:
            _run("run_sensitivity.py")
        except Exception as e:  # noqa: BLE001
            errors.append(f"sensitivity: {e}")
            status = "partial"
            print(f"敏感性重算失败：{e}", flush=True)

    if not args.skip_page:
        try:
            _run("run_build_workflow.py")
            if not args.skip_github:
                try:
                    _run("deploy_github.py")
                    github_status = "ok"
                except Exception as e:  # noqa: BLE001
                    errors.append(f"github: {e}")
                    github_status = "failed"
                    status = "partial"
                    print(f"GitHub Pages 发布失败：{e}", flush=True)
        except Exception as e:  # noqa: BLE001
            errors.append(f"page: {e}")
            status = "partial"
            print(f"页面重建失败：{e}", flush=True)

    reps = None
    if meta is not None and not args.fast:
        try:
            reps = len(select_representatives(meta))
        except Exception:  # noqa: BLE001
            pass

    obj = {
        "updated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "data_date": str(latest.date()) if latest is not None else None,
        "universe_total": int(len(univ)) if univ is not None else None,
        "broad_meta_rows": int(len(meta)) if meta is not None else None,
        "representatives": reps,
        "fast_mode": bool(args.fast),
        "github": github_status,
        "status": status,
        "errors": errors,
        "duration_sec": round(time.time() - t0, 1),
    }
    (OUT / "daily_update_status.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\n每日更新完成：数据日期 {obj['data_date']}，状态 {status}，"
        f"耗时 {obj['duration_sec']}s",
        flush=True,
    )
    if errors:
        print("错误/警告：", flush=True)
        for e in errors:
            print(" -", e, flush=True)


if __name__ == "__main__":
    main()
