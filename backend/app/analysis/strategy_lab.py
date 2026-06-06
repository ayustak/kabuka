"""戦略ラボ: 売りルール検証 / リスク管理 / ウォークフォワード（精度・実用性の向上）。

すべて月次粒度で統一（売りルールは月末終値で判定＝場中ストップは未考慮。実運用では
やや楽観／保守の両面があるため「目安」と理解する）。生存者バイアス除去のPITユニバース上で評価。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import COST_BPS, DATA_DIR
from app.analysis.backtest.factors import compute_factors
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements

TAX = 0.20315
_COST = COST_BPS / 10000.0


def _load():
    prices = load_prices()
    stmt = load_statements()
    me = prices.resample("ME").last()
    factors = compute_factors(prices)
    factors.update(build_fundamental_factors_jq(prices, stmt))
    # value傾斜スコア
    vt = (factors["value_bp"].rank(axis=1, pct=True) * 0.4
          + factors["value_ep"].rank(axis=1, pct=True) * 0.4
          + factors["reversal_1m"].rank(axis=1, pct=True) * 0.2)
    # メンバーシップ（年月対応）
    mem = load_membership(); mem["ym"] = mem["month_end"].dt.to_period("M")
    by_ym = mem.groupby("ym")["code"].agg(set)
    sec = mem.dropna(subset=["s33"]).drop_duplicates("code").set_index("code")["s33"].to_dict()
    topix = pd.read_parquet(DATA_DIR / "topix_index.parquet")["C"].resample("ME").last()
    return me, vt, by_ym, sec, topix


def _members(by_ym, t):
    return by_ym.get(t.to_period("M"), set())


def _metrics(equity: pd.Series, topix: pd.Series, n_trades: int = 0) -> dict:
    r = equity.pct_change().dropna()
    n = len(equity)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (12 / n) - 1 if n > 1 else float("nan")
    sharpe = (r.mean() * 12) / (r.std() * np.sqrt(12)) if r.std() > 0 else float("nan")
    dd = float((equity / equity.cummax() - 1).min())
    tx = topix.reindex(equity.index).ffill()
    tcagr = (tx.iloc[-1] / tx.iloc[0]) ** (12 / n) - 1 if n > 1 else float("nan")
    return {"cagr_pct": round(cagr * 100, 1), "sharpe": round(float(sharpe), 2),
            "max_dd_pct": round(dd * 100, 1), "excess_pct": round((cagr - tcagr) * 100, 1),
            "n_trades": n_trades}


# ---------- ① 売りルール検証 ----------
def sell_rule_compare(top_n: int = 20, sector_cap: int | None = None) -> list[dict]:
    """各種売りルールを value傾斜ロングオンリーに適用して比較（等加重・月次）。"""
    me, vt, by_ym, sec, topix = _load()
    dates = [t for t in vt.index if len(_members(by_ym, t)) >= top_n and t in me.index]

    configs = [
        {"name": "順位脱落のみ(基準)", "sl": None, "ts": None, "tp": None, "hold": None},
        {"name": "+損切り -15%", "sl": 0.15, "ts": None, "tp": None, "hold": None},
        {"name": "+トレーリング -20%", "sl": None, "ts": 0.20, "tp": None, "hold": None},
        {"name": "+利確 +30%", "sl": None, "ts": None, "tp": 0.30, "hold": None},
        {"name": "+期間切れ 6ヶ月", "sl": None, "ts": None, "tp": None, "hold": 6},
        {"name": "損切-15%+トレ-20%", "sl": 0.15, "ts": 0.20, "tp": None, "hold": None},
    ]
    out = []
    for cfg in configs:
        eq, trades = _run_sell_sim(me, vt, by_ym, sec, dates, top_n, cfg, sector_cap)
        out.append({"rule": cfg["name"], **_metrics(eq, topix, trades)})
    return out


def _run_sell_sim(me, vt, by_ym, sec, dates, top_n, cfg, sector_cap):
    cash, holds = 1.0, {}   # holds: code -> {w(投資比率の取得時価値), entry, peak, months}
    equity, trades = [], 0

    def topn_for(t):
        sc = vt.loc[t]; members = _members(by_ym, t)
        sc = sc[[c for c in sc.index if c in members]].dropna().sort_values(ascending=False)
        picks = list(sc.index)
        if sector_cap:  # セクター集中上限
            cnt, sel = {}, []
            for c in picks:
                s = sec.get(c, "?")
                if cnt.get(s, 0) < sector_cap:
                    sel.append(c); cnt[s] = cnt.get(s, 0) + 1
                if len(sel) >= top_n:
                    break
            return sel
        return picks[:top_n]

    prev_prices = None
    for i, t in enumerate(dates):
        px = me.loc[t]
        # 既存保有を時価更新＆売り判定
        if i > 0:
            for c in list(holds):
                p = px.get(c); h = holds[c]
                if not np.isfinite(p):
                    cash += h["val"]; del holds[c]; trades += 1; continue
                ret = p / prev_prices.get(c, p) if prev_prices is not None and np.isfinite(prev_prices.get(c, np.nan)) else 1.0
                h["val"] *= ret
                h["peak"] = max(h["peak"], p); h["months"] += 1
                pnl = p / h["entry"] - 1
                sell = False
                if cfg["sl"] and pnl <= -cfg["sl"]:
                    sell = True
                if cfg["ts"] and p <= h["peak"] * (1 - cfg["ts"]):
                    sell = True
                if cfg["tp"] and pnl >= cfg["tp"]:
                    sell = True
                if cfg["hold"] and h["months"] >= cfg["hold"]:
                    sell = True
                if sell:
                    cash += h["val"] * (1 - _COST); del holds[c]; trades += 1
        # リバランス: ターゲット上位から、順位脱落を売り、新規を買う
        targets = set(topn_for(t))
        for c in list(holds):
            if c not in targets:
                cash += holds[c]["val"] * (1 - _COST); del holds[c]; trades += 1
        total = cash + sum(h["val"] for h in holds.values())
        target_w = total / top_n
        for c in topn_for(t):
            if c in holds:
                continue
            p = px.get(c)
            if not np.isfinite(p) or cash < target_w:
                continue
            buy = min(target_w, cash)
            cash -= buy; holds[c] = {"val": buy * (1 - _COST), "entry": p, "peak": p, "months": 0}
            trades += 1
        equity.append((t, cash + sum(h["val"] for h in holds.values())))
        prev_prices = px
    return pd.Series({t: v for t, v in equity}), trades


# ---------- ② リスク管理（セクター上限の効果） ----------
def risk_compare(top_n: int = 20) -> list[dict]:
    """等加重 vs セクター集中上限ありで、リスク調整後がどう変わるか。"""
    base = sell_rule_compare(top_n, sector_cap=None)[0]
    capped = sell_rule_compare(top_n, sector_cap=3)[0]
    base["variant"] = "上限なし(等加重)"; capped["variant"] = "セクター上限3銘柄"
    return [base, capped]


# ---------- ③ ウォークフォワード（年次でのαの安定性） ----------
def walk_forward(top_n: int = 20) -> list[dict]:
    """value傾斜の対TOPIX超過を年ごとに分解し、レジーム間の安定性を見る。"""
    me, vt, by_ym, sec, topix = _load()
    fwd = me.shift(-1) / me - 1
    tfwd = topix.shift(-1) / topix - 1
    rows = []
    for t in vt.index:
        members = _members(by_ym, t)
        if len(members) < top_n or t not in fwd.index or t not in tfwd.index or not np.isfinite(tfwd[t]):
            continue
        sc = vt.loc[t]; sc = sc[[c for c in sc.index if c in members]].dropna()
        top = sc.sort_values(ascending=False).head(top_n).index
        pr = fwd.loc[t, top].mean()
        rows.append({"year": t.year, "pick": pr, "topix": tfwd[t]})
    df = pd.DataFrame(rows)
    out = []
    for y, g in df.groupby("year"):
        ex = (g["pick"].mean() - g["topix"].mean())
        out.append({"year": int(y), "months": len(g),
                    "pick_ann_pct": round((1 + g["pick"].mean())**12 * 100 - 100, 1),
                    "topix_ann_pct": round((1 + g["topix"].mean())**12 * 100 - 100, 1),
                    "excess_ann_pct": round(((1 + g["pick"].mean())**12 - (1 + g["topix"].mean())**12) * 100, 1),
                    "win": "○" if ex > 0 else "×"})
    return out
