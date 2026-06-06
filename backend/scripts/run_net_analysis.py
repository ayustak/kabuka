"""① 税引後・実執行の到達点分析（value傾斜ロングオンリー vs TOPIX）。

バックテストの「対TOPIX超過」は税引前・理想執行。ここに現実を入れて正味の優位を見る:
- 取引コスト（既に控除済の片道20bps）に加え、
- 税: 月次リバランスで利益はその年に実現 → 年次のプラス分に 20.315% 課税（課税口座）。NISAは非課税。
- 100株単位制約: 運用額別に「上位分位の何銘柄を実際に保有できるか」＝分散の現実。

使い方: cd backend; ../.venv/bin/python scripts/run_net_analysis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from app.config import COST_BPS, N_QUANTILES
from app.analysis.backtest.engine import _quantile_returns, _annualize, _max_drawdown
from app.analysis.backtest.factors import compute_factors, forward_monthly_return
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements
from run_backtest_pit import _apply_mask, _membership_mask, _topix_monthly_forward, _value_tilted_composite

TAX = 0.20315


def _after_tax_annual(monthly: pd.Series) -> pd.Series:
    """月次リターン→年次に集計し、プラスの年に課税（課税口座近似）。"""
    yearly = (1 + monthly.fillna(0)).groupby(monthly.index.year).prod() - 1
    return yearly.apply(lambda r: r - TAX * r if r > 0 else r)


def _cagr_from_yearly(yearly: pd.Series) -> float:
    n = len(yearly)
    return float((1 + yearly).prod() ** (1 / n) - 1) if n else float("nan")


def main() -> None:
    print("=" * 72)
    print("kabuka ① 税引後・実執行の到達点（value傾斜ロングオンリー vs TOPIX）")
    print("=" * 72)

    prices = load_prices()
    stmt = load_statements()
    mask = _membership_mask(prices, load_membership())

    factors = compute_factors(prices)
    factors.update(build_fundamental_factors_jq(prices, stmt))
    factors = {k: _apply_mask(v, mask) for k, v in factors.items()}
    vt = _apply_mask(_value_tilted_composite(factors), mask)

    fwd = forward_monthly_return(prices)
    out = _quantile_returns(vt, fwd, N_QUANTILES, COST_BPS)
    top = out["q_rets"][N_QUANTILES - 1].dropna()       # 上位分位ロングオンリー（コスト後）
    topix = _topix_monthly_forward(prices.index).reindex(top.index)

    # 年率（コスト後・税引前）
    g_ret, g_vol, g_sharpe = _annualize(top)
    t_ret, _, t_sharpe = _annualize(topix.dropna())
    dd = _max_drawdown((1 + top.fillna(0)).cumprod())

    # 税引後CAGR（課税口座 / NISA非課税）
    strat_y = (1 + top.fillna(0)).groupby(top.index.year).prod() - 1
    topix_y = (1 + topix.fillna(0)).groupby(topix.index.year).prod() - 1
    cagr_pretax = _cagr_from_yearly(strat_y)
    cagr_aftertax = _cagr_from_yearly(_after_tax_annual(top))     # 課税口座
    cagr_nisa = cagr_pretax                                        # NISAは非課税=税引前と同じ
    cagr_topix = _cagr_from_yearly(topix_y)

    print(f"\n期間: {top.index.min().date()}〜{top.index.max().date()}（{len(top)}ヶ月）")
    print("\n【年率パフォーマンス（コスト後）】")
    print(f"  value傾斜ロングオンリー: 年率 {g_ret*100:.1f}% / ボラ {g_vol*100:.1f}% / シャープ {g_sharpe:.2f} / 最大DD {dd*100:.1f}%")
    print(f"  TOPIX                  : 年率 {t_ret*100:.1f}% / シャープ {t_sharpe:.2f}")
    print("\n【CAGR比較】")
    print(f"  戦略 税引前(NISA相当)   : {cagr_nisa*100:.1f}%/年")
    print(f"  戦略 税引後(課税口座)   : {cagr_aftertax*100:.1f}%/年")
    print(f"  TOPIX                   : {cagr_topix*100:.1f}%/年")
    print(f"  → 正味超過 (税引前/NISA): +{(cagr_nisa-cagr_topix)*100:.1f}%/年")
    print(f"  → 正味超過 (税引後・課税): +{(cagr_aftertax-cagr_topix)*100:.1f}%/年")

    # 100株単位の分散（直近月の上位分位構成で試算）
    last = prices.index[-1]
    last_scores = vt.iloc[vt.index.get_indexer([prices.resample('ME').last().index[-1]], method='nearest')[0]]
    members = last_scores.dropna()
    ranks = members.rank(pct=True)
    top_codes = ranks[ranks > (N_QUANTILES - 1) / N_QUANTILES].index
    last_prices = prices.loc[last, top_codes].dropna()
    lot = last_prices * 100  # 1単元(100株)の金額
    print(f"\n【100株単位の分散（直近 上位分位 {len(last_prices)}銘柄）】")
    print(f"  1単元の中央値: {lot.median():,.0f}円 / 全銘柄を1単元ずつ揃える総額: {lot.sum()/1e6:.1f}百万円")
    for cap in [1_000_000, 3_000_000, 5_000_000, 10_000_000]:
        # 安い順に1単元ずつ買えるだけ買ったときの保有銘柄数
        n = (lot.sort_values().cumsum() <= cap).sum()
        print(f"  運用額 {cap/1e6:.0f}百万円 → 保有可能 {n}/{len(last_prices)}銘柄（手数料ドラッグ {39600/cap*100:.1f}%/年）")

    print("\n注: 月次全入替前提の税近似（年次プラス分に課税）。実際はNISA枠・損益通算・保有継続で軽減余地あり。")


if __name__ == "__main__":
    main()
