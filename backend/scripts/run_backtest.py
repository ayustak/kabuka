"""無料データ(yfinance)で株価ファクターの分位バックテストを回し、結果を表示する。

使い方:
    cd backend
    ../.venv/bin/python scripts/run_backtest.py

推奨アプローチの一次検証:
「日経225近似ユニバースで、株価ファクターのロングショート/対TOPIX超過が
 コスト控除後にプラスのαを生むか」を ¥0 で確認し、さらに
「そのαは多重検定を補正しても偶然でないか(Deflated Sharpe)」まで踏み込む。
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
from app.analysis.backtest.metrics import deflated_sharpe_ratio
from app.ingestion.prices import fetch_prices
from app.ingestion.universe import BENCHMARKS, universe_tickers

LS_COL = f"LS(Q{N_QUANTILES}-Q1)"


def _add_composite(factors: dict[str, pd.DataFrame]) -> None:
    """合成ファクター: ICがプラスだった軸(6ヶ月モメンタム+短期リバーサル)を
    クロスセクション順位の平均で合成。単独より安定するかを見る。"""
    parts = []
    for key in ("mom_6_1", "reversal_1m"):
        if key in factors:
            parts.append(factors[key].rank(axis=1, pct=True))
    if parts:
        factors["composite"] = sum(parts) / len(parts)


def main() -> None:
    print("=" * 72)
    print("kabuka 無料データ・ファクターバックテスト（一次検証 + 統計的有意性）")
    print("=" * 72)

    close = fetch_prices()
    uni = [t for t in universe_tickers() if t in close.columns]
    bench_col = BENCHMARKS["TOPIX_ETF"]
    have_bench = bench_col in close.columns

    print(f"ユニバース: {len(uni)} 銘柄  期間: {close.index.min().date()}〜{close.index.max().date()}  分位数: {N_QUANTILES}")
    print(f"ベンチマーク(TOPIX ETF {bench_col}): {'取得済' if have_bench else '欠損→等加重で代用'}")

    uni_close = close[uni]
    factors = compute_factors(uni_close)
    _add_composite(factors)

    # value/quality（EDINET財務キャッシュがあれば追加。無ければスキップ）
    try:
        from app.analysis.backtest.factors_fundamental import (
            build_fundamental_factors,
            build_value_quality_composite,
        )
        from app.ingestion.financials import load_financials

        fin = load_financials()
        fund = build_fundamental_factors(uni_close, fin)
        factors.update(fund)
        factors["value_quality"] = build_value_quality_composite(fund)
        print(f"value/quality 追加: {fin['sec_code'].nunique()} 銘柄分の財務を結合")
    except FileNotFoundError:
        print("value/quality: 財務キャッシュ未作成のためスキップ（EDINETキー設定後に有効化）")

    fwd = forward_monthly_return(uni_close)

    if have_bench:
        bench_me = close[bench_col].resample("ME").last()
        bench_ret = bench_me.pct_change().shift(-1)
    else:
        bench_ret = fwd.mean(axis=1)
    bench_ret = bench_ret.rename("bench")

    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    # --- (1) 既定コストでのファクター別サマリ + DSR ---
    print(f"\n【(1) ファクター別サマリ（往復 {COST_BPS*2/100:.2f}%控除後）】")
    rows, ls_series, monthly_sharpes = [], {}, {}
    for name, fac in factors.items():
        res, table = run_factor(name, fac, fwd, bench_ret, N_QUANTILES, COST_BPS)
        rows.append(res)
        ls = table[LS_COL].dropna()
        ls_series[name] = ls
        monthly_sharpes[name] = ls.mean() / ls.std() if ls.std() > 0 else float("nan")

    df = pd.DataFrame([r.__dict__ for r in rows]).set_index("name")
    print(df[["ic_mean", "ic_ir", "ls_ann_return", "ls_sharpe", "ls_max_dd",
              "long_ann_excess", "long_hit_rate", "n_months"]].to_string())

    # --- (2) Deflated Sharpe Ratio（多重検定補正） ---
    all_sharpes = list(monthly_sharpes.values())
    best = max(monthly_sharpes, key=lambda k: (monthly_sharpes[k] if np.isfinite(monthly_sharpes[k]) else -9))
    dsr = deflated_sharpe_ratio(ls_series[best], all_sharpes)
    print(f"\n【(2) 統計的有意性 Deflated Sharpe Ratio】試行数 N={dsr['n_trials']}（試したファクター数）")
    print(f"  最良ファクター: {best}（月次シャープ {monthly_sharpes[best]:.3f}）")
    print(f"  多重検定で期待される基準シャープ SR*(per-month): {dsr['sr_star']:.3f}")
    print(f"  DSR = {dsr['dsr']:.3f}  → 0.95超で『偶然でない』と言える水準")
    verdict = "本物らしい" if dsr["dsr"] > 0.95 else "偶然の範囲を否定できない"
    print(f"  判定: {verdict}")

    # --- (3) コスト感度スイープ（ロングショート年率リターン） ---
    print("\n【(3) コスト感度スイープ：ロングショート年率リターン】")
    sweeps = [0, 10, 20, 40]
    sweep_rows = []
    for name, fac in factors.items():
        row = {"factor": name}
        for c in sweeps:
            res, _ = run_factor(name, fac, fwd, bench_ret, N_QUANTILES, c)
            row[f"{c}bps"] = res.ls_ann_return
        sweep_rows.append(row)
    print(pd.DataFrame(sweep_rows).set_index("factor").to_string())

    print("\n読み方 / 注意:")
    print(" - ic_mean 0.02〜0.05で実務的に有効水準。ls_sharpeはプロでも全コスト込み1前後が上限級。")
    print(" - DSRが0.95未満なら、見えているαは多重検定の偶然の範囲を否定できない。")
    print(" - ユニバースは現構成銘柄のみ＝生存者バイアスで上振れ方向。合否はJ-Quants(Standard)のPIT再検証まで保留。")
    print(" - value/quality(本命)は履歴財務が要る → EDINET/J-Quants導入後に追加。yfinanceのファンダは現時点値のみで非PIT。")


if __name__ == "__main__":
    main()
