"""投資シミュレーション・エンジン（実投資前のペーパー検証用）。

value傾斜スコア上位N銘柄を等加重で保有し、指定頻度でリバランスする戦略を、
**100株単位・取引コスト・税（課税口座/NISA）** を厳密に反映して過去データで再現する。

ブラウザのシミュレーターから初期資金・銘柄数・リバランス頻度・口座種別を変えて
「もしこう投資していたら」を即座に試せるようにするためのコア。

入力データ（build_dashboard_data.py が事前生成・キャッシュ）:
- sim_px.parquet     : 月末株価（wide, columns=code）
- sim_scores.parquet : value傾斜スコア（月末×code, 構成銘柄のみ非NaN）
- sim_topix.parquet  : TOPIX 月末水準
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import DATA_DIR

TAX = 0.20315
_REBALANCE = {"M": 1, "Q": 3, "H": 6, "Y": 12}  # 月/四半期/半年/年

_cache: dict = {}


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    if not _cache:
        _cache["px"] = pd.read_parquet(DATA_DIR / "sim_px.parquet")
        _cache["scores"] = pd.read_parquet(DATA_DIR / "sim_scores.parquet")
        _cache["topix"] = pd.read_parquet(DATA_DIR / "sim_topix.parquet")["C"]
    return _cache["px"], _cache["scores"], _cache["topix"]


def _price_asof(px: pd.DataFrame, code: str, t) -> float:
    """月末tの株価。欠損なら直近の既知値（廃止銘柄の最終売却用）。"""
    if code in px.columns:
        v = px.at[t, code]
        if np.isfinite(v):
            return float(v)
        s = px[code].loc[:t].dropna()
        if len(s):
            return float(s.iloc[-1])
    return float("nan")


def simulate(initial_capital: float = 3_000_000, n_holdings: int = 20,
             rebalance: str = "Q", account: str = "nisa",
             cost_bps: float = 20.0, start: str | None = None) -> dict:
    """シミュレーション実行。資産推移とメトリクスを返す。"""
    px, scores, topix = _load()
    cost = cost_bps / 10000.0
    step = _REBALANCE.get(rebalance, 3)

    # 評価月（構成銘柄がn_holdings以上ある月）
    months = scores.index[scores.notna().sum(axis=1) >= n_holdings]
    if start:
        months = months[months >= pd.Timestamp(start)]
    months = list(months)
    if len(months) < 6:
        return {"error": "対象期間が短すぎます"}
    reb_set = set(months[::step])

    cash = float(initial_capital)
    holds: dict[str, dict] = {}  # code -> {sh, cost(平均取得単価)}
    total_tax = 0.0
    total_cost = 0.0
    equity, hold_counts = [], []

    for t in months:
        if t in reb_set:
            sc = scores.loc[t].dropna().sort_values(ascending=False)
            targets = list(sc.head(n_holdings).index)
            tset = set(targets)
            # 現在価値
            pv = cash + sum(h["sh"] * _price_asof(px, c, t) for c, h in holds.items())
            tpv = pv / n_holdings  # 1銘柄あたり目標額

            # 売り（ターゲット外を全売却）
            for c in list(holds):
                if c not in tset:
                    p = _price_asof(px, c, t)
                    sh = holds[c]["sh"]
                    proceeds = sh * p
                    gain = proceeds - sh * holds[c]["cost"]
                    tax = max(0.0, gain) * TAX if account == "taxable" else 0.0
                    fee = proceeds * cost
                    cash += proceeds - fee - tax
                    total_tax += tax
                    total_cost += fee
                    del holds[c]

            # 買い/調整（100株単位で目標額に寄せる）
            for c in targets:
                p = _price_asof(px, c, t)
                if not np.isfinite(p) or p <= 0:
                    continue
                lot_val = p * 100
                tgt_sh = int(tpv // lot_val) * 100
                cur = holds.get(c, {"sh": 0})["sh"]
                if tgt_sh > cur:  # 買い増し
                    buy = tgt_sh - cur
                    spend = buy * p
                    fee = spend * cost
                    if spend + fee <= cash and buy > 0:
                        prev = holds.get(c, {"sh": 0, "cost": p})
                        new_sh = prev["sh"] + buy
                        holds[c] = {"sh": new_sh,
                                    "cost": (prev["sh"] * prev.get("cost", p) + buy * p) / new_sh}
                        cash -= spend + fee
                        total_cost += fee
                elif tgt_sh < cur:  # 一部売却
                    sell = cur - tgt_sh
                    proceeds = sell * p
                    gain = proceeds - sell * holds[c]["cost"]
                    tax = max(0.0, gain) * TAX if account == "taxable" else 0.0
                    fee = proceeds * cost
                    cash += proceeds - fee - tax
                    total_tax += tax
                    total_cost += fee
                    if tgt_sh == 0:
                        del holds[c]
                    else:
                        holds[c]["sh"] = tgt_sh
            hold_counts.append(len(holds))

        pv = cash + sum(h["sh"] * _price_asof(px, c, t) for c, h in holds.items())
        equity.append((t, pv))

    eq = pd.Series({t: v for t, v in equity})
    tpx = topix.reindex(eq.index).ffill()
    tpx_norm = initial_capital * tpx / tpx.iloc[0]

    monthly_ret = eq.pct_change().dropna()
    n = len(eq)
    cagr = (eq.iloc[-1] / initial_capital) ** (12 / n) - 1
    topix_cagr = (tx := tpx_norm.iloc[-1] / initial_capital) ** (12 / n) - 1
    sharpe = (monthly_ret.mean() * 12) / (monthly_ret.std() * np.sqrt(12)) if monthly_ret.std() > 0 else float("nan")
    dd = float((eq / eq.cummax() - 1).min())

    curve = [{"date": t.strftime("%Y-%m"), "portfolio": round(eq[t]), "topix": round(tx_)}
             for t, tx_ in tpx_norm.items()]

    return {
        "params": {"initial_capital": initial_capital, "n_holdings": n_holdings,
                   "rebalance": rebalance, "account": account, "cost_bps": cost_bps},
        "final_value": round(eq.iloc[-1]),
        "topix_final_value": round(float(tpx_norm.iloc[-1])),
        "profit": round(eq.iloc[-1] - initial_capital),
        "cagr_pct": round(cagr * 100, 1),
        "topix_cagr_pct": round(topix_cagr * 100, 1),
        "excess_pct": round((cagr - topix_cagr) * 100, 1),
        "sharpe": round(float(sharpe), 2),
        "max_dd_pct": round(dd * 100, 1),
        "total_tax": round(total_tax),
        "total_cost": round(total_cost),
        "avg_holdings": round(float(np.mean(hold_counts)), 1) if hold_counts else 0,
        "period": f"{eq.index.min().date()} 〜 {eq.index.max().date()}",
        "equity_curve": curve,
    }


if __name__ == "__main__":
    import json
    for acc in ("nisa", "taxable"):
        for reb in ("M", "Q", "Y"):
            r = simulate(3_000_000, 20, reb, acc)
            print(f"{acc:8} {reb}: 最終 {r['final_value']:>12,}円 CAGR {r['cagr_pct']}% "
                  f"対TOPIX {r['excess_pct']:+}% 税 {r['total_tax']:,} 平均保有 {r['avg_holdings']}")
