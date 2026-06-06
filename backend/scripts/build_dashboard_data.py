"""ダッシュボード用データ（戦略サマリ＋買い候補）をJSONに書き出す。

重い計算（バックテスト）は事前にここで実行してキャッシュし、APIは軽く返す設計。
使い方: cd backend; ../.venv/bin/python scripts/build_dashboard_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from app.config import COST_BPS, DATA_DIR, N_QUANTILES
from app.analysis.backtest.engine import _annualize, _max_drawdown, _quantile_returns
from app.analysis.backtest.factors import compute_factors, forward_monthly_return
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements
from app.signals.value_signal import generate_value_signal
from run_backtest_pit import _apply_mask, _membership_mask, _topix_monthly_forward, _value_tilted_composite

TAX = 0.20315


def _cagr(yearly: pd.Series) -> float:
    n = len(yearly)
    return float((1 + yearly).prod() ** (1 / n) - 1) if n else float("nan")


def main() -> None:
    prices = load_prices()
    stmt = load_statements()
    mask = _membership_mask(prices, load_membership())
    factors = compute_factors(prices)
    factors.update(build_fundamental_factors_jq(prices, stmt))
    factors = {k: _apply_mask(v, mask) for k, v in factors.items()}
    vt = _apply_mask(_value_tilted_composite(factors), mask)

    fwd = forward_monthly_return(prices)
    out = _quantile_returns(vt, fwd, N_QUANTILES, COST_BPS)
    top = out["q_rets"][N_QUANTILES - 1].dropna()
    topix = _topix_monthly_forward(prices.index).reindex(top.index)

    g_ret, g_vol, g_sharpe = _annualize(top)
    dd = _max_drawdown((1 + top.fillna(0)).cumprod())
    strat_y = (1 + top.fillna(0)).groupby(top.index.year).prod() - 1
    topix_y = (1 + topix.fillna(0)).groupby(topix.index.year).prod() - 1
    aftertax_y = strat_y.apply(lambda r: r - TAX * r if r > 0 else r)

    cagr_pretax, cagr_aftertax, cagr_topix = _cagr(strat_y), _cagr(aftertax_y), _cagr(topix_y)

    # 累積リターン（チャート用）
    cum = pd.DataFrame({
        "strategy": (1 + top.fillna(0)).cumprod(),
        "topix": (1 + topix.fillna(0)).cumprod(),
    })
    equity = [{"date": d.strftime("%Y-%m"), "strategy": round(r.strategy, 3), "topix": round(r.topix, 3)}
              for d, r in cum.iterrows()]

    summary = {
        "period": f"{top.index.min().date()} 〜 {top.index.max().date()}",
        "universe": "TOPIX500（時点別・生存者バイアス除去）",
        "strategy": "value傾斜（B/P 0.4 + 益回り 0.4 + 短期反転 0.2）ロングオンリー上位分位",
        "ann_return_pct": round(g_ret * 100, 1),
        "ann_vol_pct": round(g_vol * 100, 1),
        "sharpe": round(g_sharpe, 2),
        "max_dd_pct": round(dd * 100, 1),
        "cagr_pretax_pct": round(cagr_pretax * 100, 1),
        "cagr_aftertax_pct": round(cagr_aftertax * 100, 1),
        "cagr_topix_pct": round(cagr_topix * 100, 1),
        "excess_nisa_pct": round((cagr_pretax - cagr_topix) * 100, 1),
        "excess_taxable_pct": round((cagr_aftertax - cagr_topix) * 100, 1),
        "cost_bps_oneway": COST_BPS,
        "equity_curve": equity,
        "note": "コスト後・税近似は月次全入替前提。NISA・低回転で改善余地。生存者バイアス除去済だが過去実績は将来を保証しない。",
    }
    (DATA_DIR / "strategy_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    sig = generate_value_signal(top_n=30)
    sig_obj = {"asof": sig.attrs.get("asof"), "holdings": sig.to_dict(orient="records")}
    (DATA_DIR / "signals_latest.json").write_text(
        json.dumps(sig_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    print("書き出し完了: data/strategy_summary.json, data/signals_latest.json")
    print(f"  戦略CAGR 税引前 {summary['cagr_pretax_pct']}% / 税引後 {summary['cagr_aftertax_pct']}% / TOPIX {summary['cagr_topix_pct']}%")


if __name__ == "__main__":
    main()
