"""② LightGBMでファクターを非線形結合し、Purged CVで評価（PIT・対TOPIX）。

使い方:
    cd backend
    ../.venv/bin/python scripts/run_model_lgbm.py
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
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.analysis.backtest.metrics import deflated_sharpe_ratio
from app.analysis.backtest.model_lgbm import (
    assemble_panel, feature_importance, oof_to_factor, purged_cv_predict,
)
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements

# run_backtest_pit のヘルパを再利用
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_backtest_pit import _apply_mask, _membership_mask, _topix_monthly_forward, _value_tilted_composite  # noqa: E402

LS_COL = f"LS(Q{N_QUANTILES}-Q1)"


def _summ(name, fac, fwd, bench, monthly_sharpes, ls_series):
    res, table = run_factor(name, fac, fwd, bench, N_QUANTILES, COST_BPS)
    ls = table[LS_COL].dropna()
    ls_series[name] = ls
    monthly_sharpes[name] = ls.mean() / ls.std() if ls.std() > 0 else float("nan")
    return res


def main() -> None:
    print("=" * 72)
    print("kabuka ② LightGBM クロスセクション・モデル（Purged CV / PIT / 対TOPIX）")
    print("=" * 72)

    prices = load_prices()
    stmt = load_statements()
    membership = load_membership()
    mask = _membership_mask(prices, membership)

    factors = compute_factors(prices)
    factors.update(build_fundamental_factors_jq(prices, stmt))
    factors = {k: _apply_mask(v, mask) for k, v in factors.items()}
    factors["value_tilt"] = _apply_mask(_value_tilted_composite(factors), mask)

    fwd = forward_monthly_return(prices)
    bench = _topix_monthly_forward(prices.index).rename("bench")

    # --- LightGBM: 特徴量組成 → Purged CV out-of-fold 予測 ---
    panel = assemble_panel(factors, fwd)
    print(f"学習サンプル: {len(panel):,} 行（月×銘柄） / 特徴量 {sum(f in panel for f in ['value_bp','mom_6_1'])}+本")
    oof = purged_cv_predict(panel, n_splits=5, embargo=1)
    lgbm_factor = _apply_mask(oof_to_factor(oof, factors["value_bp"]), mask)

    # --- 比較: 単一最強(value_bp) vs value傾斜合成 vs LightGBM ---
    monthly_sharpes, ls_series, rows = {}, {}, []
    for name, fac in [("value_bp", factors["value_bp"]),
                      ("value_tilt", factors["value_tilt"]),
                      ("lgbm", lgbm_factor)]:
        rows.append(_summ(name, fac, fwd, bench, monthly_sharpes, ls_series))

    df = pd.DataFrame([r.__dict__ for r in rows]).set_index("name")
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")
    print("\n【ベンチ比較（往復 {:.2f}%控除後・対TOPIX超過）】".format(COST_BPS * 2 / 100))
    print(df[["ic_mean", "ic_ir", "ls_ann_return", "ls_sharpe", "ls_max_dd",
              "long_ann_excess", "long_hit_rate", "n_months"]].to_string())

    # DSR（3手法での多重検定補正）
    best = max(monthly_sharpes, key=lambda k: monthly_sharpes[k] if np.isfinite(monthly_sharpes[k]) else -9)
    dsr = deflated_sharpe_ratio(ls_series[best], list(monthly_sharpes.values()))
    print(f"\n【DSR】最良={best}（月次SR {monthly_sharpes[best]:.3f}） SR*={dsr['sr_star']:.3f} DSR={dsr['dsr']:.3f}"
          f" → {'本物らしい(>0.95)' if dsr['dsr']>0.95 else '偶然を否定できない(<0.95)'}")

    print("\n【特徴量重要度(gain)】")
    print(feature_importance(panel).round(0).to_string())

    print("\n注: out-of-fold(未学習区間)予測のみで評価＝過学習を排した汎化性能。")


if __name__ == "__main__":
    main()
