"""推奨のフォワード成績を記録・評価する（精度向上ループの中核）。

2系統:
(a) ライブ記録: log_snapshot() が「今日の期間別 上位推奨」を保存。実時間で本物の実績が貯まる。
(b) ヒストリカル評価: historical_accuracy() が過去データで「この推奨が歴史的にどれだけ当たってきたか」
    を期間別に算出（的中率・対TOPIX勝率・IC）。今すぐ精度の目安が得られる（=過去実績、将来保証なし）。

期間→評価ホライズン: short≈1ヶ月 / mid≈3ヶ月 / long≈12ヶ月（保有日数に対応）。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from app.config import DATA_DIR
from app.analysis.backtest.factors import compute_factors
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements
from app.signals.recommend import HORIZONS, _composite

_LOG = DATA_DIR / "reco_log.parquet"
_HORIZON_MONTHS = {"short": 1, "mid": 3, "long": 12}


def _factors():
    prices = load_prices()
    stmt = load_statements()
    factors = compute_factors(prices)
    factors.update(build_fundamental_factors_jq(prices, stmt))
    return prices, factors


def _members_by_month() -> pd.DataFrame:
    m = load_membership()
    m["ym"] = m["month_end"].dt.to_period("M")
    return m


# ---------- (a) ライブ記録 ----------
def log_snapshot(top_n: int = 15) -> dict:
    """今日の期間別 上位推奨をログに追記（重複日はスキップ）。"""
    from app.signals.recommend import recommend
    today = dt.date.today().isoformat()
    existing = pd.read_parquet(_LOG) if _LOG.exists() else pd.DataFrame()
    rows = []
    for h in HORIZONS:
        r = recommend(h, top_n)
        for p in r["picks"]:
            rows.append({"date": today, "horizon": h, "code": p["code"],
                         "name": p["name"], "score": p["score"], "price": p["price"]})
    new = pd.DataFrame(rows)
    if len(existing):
        # 同一(date,horizon)は上書きせずスキップ
        done = set(zip(existing["date"], existing["horizon"]))
        new = new[~new.apply(lambda x: (x["date"], x["horizon"]) in done, axis=1)]
    combined = pd.concat([existing, new], ignore_index=True) if len(existing) else new
    combined.to_parquet(_LOG)
    return {"logged_date": today, "added": len(new), "total_rows": len(combined)}


def live_track_status() -> dict:
    """記録済み推奨の、現時点までの実現リターン（成熟・未成熟まとめ）。"""
    if not _LOG.exists():
        return {"records": 0, "note": "まだ記録がありません。log_snapshot()で蓄積開始。"}
    log = pd.read_parquet(_LOG)
    prices = load_prices()
    last = prices.index.max()
    out = []
    for _, r in log.iterrows():
        if r["code"] not in prices.columns:
            continue
        cur = prices[r["code"]].dropna()
        if cur.empty:
            continue
        ret = cur.iloc[-1] / r["price"] - 1
        out.append({**r.to_dict(), "current": round(float(cur.iloc[-1]), 1),
                    "return_pct": round(float(ret) * 100, 1)})
    df = pd.DataFrame(out)
    by_h = {}
    if len(df):
        for h, g in df.groupby("horizon"):
            by_h[h] = {"n": len(g), "avg_return_pct": round(g["return_pct"].mean(), 1),
                       "hit_positive_pct": round((g["return_pct"] > 0).mean() * 100, 0)}
    return {"records": len(log), "as_of": str(last.date()), "by_horizon": by_h,
            "detail": out[-50:]}


# ---------- (b) ヒストリカル評価 ----------
def historical_accuracy(horizon: str = "mid", top_n: int = 15) -> dict:
    """過去の各月末に出していたら、の期間別 的中実績（的中率・対TOPIX勝率・IC）。"""
    cfg = HORIZONS[horizon]
    h = _HORIZON_MONTHS[horizon]
    prices, factors = _factors()
    me = prices.resample("ME").last()

    # 期間別合成スコア（全期間パネル）
    parts, ws = [], []
    for key, w in cfg["recipe"].items():
        if key in factors:
            parts.append(factors[key].rank(axis=1, pct=True) * w)
            ws.append(w)
    score_panel = sum(parts) / sum(ws)

    # 構成銘柄マスク（年月で対応）
    mem = _members_by_month()
    by_ym = mem.groupby("ym")["code"].agg(set)

    # TOPIX 月末
    topix = pd.read_parquet(DATA_DIR / "topix_index.parquet")["C"].resample("ME").last()

    fwd = me.shift(-h) / me - 1            # h ヶ月先リターン（各銘柄）
    topix_fwd = topix.shift(-h) / topix - 1

    periods = []
    ics, picks_ret, topix_ret, wins = [], [], [], []
    # 重複窓による過大評価を避けるため、h ヶ月ごとの非重複サンプルで評価
    eval_dates = list(score_panel.index[::h])
    for t in eval_dates:
        members = by_ym.get(t.to_period("M"))
        if not members or t not in fwd.index:
            continue
        sc = score_panel.loc[t]
        sc = sc[[c for c in sc.index if c in members]].dropna()
        fr = fwd.loc[t].reindex(sc.index)
        pair = pd.concat([sc, fr], axis=1, keys=["s", "r"]).dropna()
        if len(pair) < top_n * 2 or t not in topix_fwd.index or not np.isfinite(topix_fwd[t]):
            continue
        top = pair.nlargest(top_n, "s")
        pr = float(top["r"].mean())
        tr = float(topix_fwd[t])
        ic = pair["s"].corr(pair["r"], method="spearman")
        ics.append(ic); picks_ret.append(pr); topix_ret.append(tr); wins.append(pr > tr)
        periods.append({"date": t.strftime("%Y-%m"), "pick_ret_pct": round(pr * 100, 1),
                        "topix_ret_pct": round(tr * 100, 1)})

    n = len(picks_ret)
    if n == 0:
        return {"horizon": horizon, "n_periods": 0, "note": "評価可能な期間がありません"}
    ic_arr = np.array([x for x in ics if np.isfinite(x)])
    return {
        "horizon": horizon, "label": cfg["label"], "confidence": cfg["confidence"],
        "eval_months": h, "top_n": top_n, "n_periods": n,
        "avg_pick_return_pct": round(float(np.mean(picks_ret)) * 100, 2),
        "avg_topix_return_pct": round(float(np.mean(topix_ret)) * 100, 2),
        "avg_excess_pct": round((float(np.mean(picks_ret)) - float(np.mean(topix_ret))) * 100, 2),
        "win_rate_vs_topix_pct": round(float(np.mean(wins)) * 100, 0),
        "hit_positive_pct": round(float(np.mean(np.array(picks_ret) > 0)) * 100, 0),
        "ic_mean": round(float(ic_arr.mean()), 3),
        "ic_ir": round(float(ic_arr.mean() / ic_arr.std() * np.sqrt(12 / h)), 2) if ic_arr.std() > 0 else None,
        "periods": periods,
        "note": "過去データに基づくヒストリカル実績（将来を保証しない）。",
    }


def historical_accuracy_all(top_n: int = 15) -> dict:
    return {h: historical_accuracy(h, top_n) for h in HORIZONS}


if __name__ == "__main__":
    for h in HORIZONS:
        r = historical_accuracy(h)
        if r["n_periods"]:
            print(f"{r['label']:18} 信頼度{r['confidence']:>12} | "
                  f"期間{r['n_periods']:>3} 平均{r['avg_pick_return_pct']:>6}% "
                  f"対TOPIX{r['avg_excess_pct']:+6}% 勝率{r['win_rate_vs_topix_pct']:.0f}% "
                  f"IC{r['ic_mean']:+.3f} ICIR{r['ic_ir']}")
