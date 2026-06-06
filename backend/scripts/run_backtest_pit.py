"""生存者バイアス除去版バックテスト（J-Quants v2 / TOPIX500 / 時点別メンバーシップ）。

無料MVP(run_backtest.py)との違い:
- ユニバースが「各月末時点のTOPIX500」（廃止銘柄も当時は含む）→ 生存者バイアスを排除
- 株価・財務とも J-Quants（財務は開示日DiscDate基準のPIT結合）
- 各リバランス月で、その時点の構成銘柄のみを評価対象にマスクする

使い方:
    cd backend
    ../.venv/bin/python scripts/run_backtest_pit.py
（事前に `python -m app.ingestion.jquants_collect` でデータ収集が必要）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app.config import COST_BPS, N_QUANTILES
from app.analysis.backtest.engine import run_factor
from app.analysis.backtest.factors import compute_factors, forward_monthly_return
from app.analysis.backtest.factors_fundamental import (
    build_fundamental_factors_jq,
    build_value_quality_composite,
)
from app.analysis.backtest.metrics import deflated_sharpe_ratio
from app.config import DATA_DIR
from app.ingestion.jquants import TOPIX_CODE, get_index
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements

LS_COL = f"LS(Q{N_QUANTILES}-Q1)"


def _topix_monthly_forward(index_dates: pd.DatetimeIndex) -> pd.Series:
    """真のTOPIX(0000)の翌月リターン。1回取得してparquetキャッシュ。"""
    cache = DATA_DIR / "topix_index.parquet"
    if cache.exists():
        s = pd.read_parquet(cache)["C"]
    else:
        rows = get_index(TOPIX_CODE, from_="2016-06-06", to="2024-12-31")
        s = pd.Series({pd.to_datetime(r["Date"]): r["C"] for r in rows}).sort_index()
        s.to_frame("C").to_parquet(cache)
    me = s.resample("ME").last()
    return me.pct_change().shift(-1)  # 翌月リターン


def _value_tilted_composite(factors: dict) -> pd.DataFrame:
    """value傾斜の合成: value(B/P,E/P)を主軸に短期リバーサルを少量ブレンド。
    重み value 0.4/0.4 + reversal 0.2（クロスセクション順位で合成）。"""
    parts, weights = [], []
    for key, w in [("value_bp", 0.4), ("value_ep", 0.4), ("reversal_1m", 0.2)]:
        if key in factors:
            parts.append(factors[key].rank(axis=1, pct=True) * w)
            weights.append(w)
    return sum(parts) / sum(weights)


def _membership_mask(prices: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    """月末×銘柄の True/False マスク。年月(Period)で突き合わせて日付ズレを吸収。"""
    me_index = prices.resample("ME").last().index
    mask = pd.DataFrame(False, index=me_index, columns=prices.columns)
    membership = membership.copy()
    membership["ym"] = membership["month_end"].dt.to_period("M")
    by_ym = membership.groupby("ym")["code"].agg(set)
    for t in me_index:
        members = by_ym.get(t.to_period("M"))
        if members:
            cols = [c for c in prices.columns if c in members]
            mask.loc[t, cols] = True
    return mask


def _apply_mask(factor: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """非構成銘柄を NaN にして評価対象から除外。"""
    m = mask.reindex(index=factor.index, columns=factor.columns).fillna(False)
    return factor.where(m)


def main() -> None:
    print("=" * 72)
    print("kabuka 生存者バイアス除去版バックテスト（J-Quants TOPIX500 / PIT）")
    print("=" * 72)

    prices = load_prices()
    stmt = load_statements()
    membership = load_membership()
    print(f"株価: {prices.shape[1]}銘柄 {prices.index.min().date()}〜{prices.index.max().date()}")
    print(f"財務(連結FY): {len(stmt)}件 / {stmt['code'].nunique()}銘柄")
    print(f"PITユニバース: {membership['month_end'].nunique()}ヶ月 / ユニオン{membership['code'].nunique()}銘柄")

    mask = _membership_mask(prices, membership)

    factors = compute_factors(prices)
    fund = build_fundamental_factors_jq(prices, stmt)
    factors.update(fund)
    factors["value_quality"] = build_value_quality_composite(fund)
    factors["value_tilt"] = _value_tilted_composite(factors)  # ① value傾斜の合成
    # 構成銘柄マスクを全ファクターに適用
    factors = {k: _apply_mask(v, mask) for k, v in factors.items()}

    fwd = forward_monthly_return(prices)
    # ベンチマーク: ① 真のTOPIX指数
    bench_ret = _topix_monthly_forward(prices.index).rename("bench")

    pd.set_option("display.float_format", lambda x: f"{x:.4f}")
    rows, ls_series, monthly_sharpes = [], {}, {}
    for name, fac in factors.items():
        res, table = run_factor(name, fac, fwd, bench_ret, N_QUANTILES, COST_BPS)
        rows.append(res)
        ls = table[LS_COL].dropna()
        ls_series[name] = ls
        monthly_sharpes[name] = ls.mean() / ls.std() if ls.std() > 0 else float("nan")

    df = pd.DataFrame([r.__dict__ for r in rows]).set_index("name")
    print(f"\n【ファクター別サマリ（往復 {COST_BPS*2/100:.2f}%控除後・対TOPIX超過）】")
    print(df[["ic_mean", "ic_ir", "ls_ann_return", "ls_sharpe", "ls_max_dd",
              "long_ann_excess", "long_hit_rate", "n_months"]].to_string())

    all_sharpes = list(monthly_sharpes.values())
    best = max(monthly_sharpes, key=lambda k: monthly_sharpes[k] if np.isfinite(monthly_sharpes[k]) else -9)
    dsr = deflated_sharpe_ratio(ls_series[best], all_sharpes)
    print(f"\n【Deflated Sharpe Ratio】試行N={dsr['n_trials']}  最良={best}（月次SR {monthly_sharpes[best]:.3f}）")
    print(f"  SR*(per-month)={dsr['sr_star']:.3f}  DSR={dsr['dsr']:.3f}  判定: "
          f"{'本物らしい(>0.95)' if dsr['dsr'] > 0.95 else '偶然を否定できない(<0.95)'}")

    print("\n注: 生存者バイアスを除いた本検証。ベンチは真のTOPIX指数(0000)。超過はコスト後・税引前。")


if __name__ == "__main__":
    main()
