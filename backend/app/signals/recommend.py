"""期間別（短期・中期・長期）の買い候補と売りルールを考察・提示する推奨エンジン。

【設計方針 — 誠実さ最優先】
- 中期/長期は検証済みファクター（バリュー、value傾斜）に基づく＝信頼度「高」。
- 短期は未検証の実験的シグナル（短期リバーサル＋モメンタム）＝信頼度「低」と明示する。
- 「予測」と称して曖昧に断定せず、根拠ファクターと透明な売りルールをセットで提示する。

各期間の出力: 買い候補（スコア順）＋ 各銘柄の根拠 ＋ 推奨保有期間 ＋ 売り条件（損切り/利確/順位脱落/期間切れ）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.backtest.factors import compute_factors
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements

# 期間ごとの設計（根拠ファクターの重み・保有期間・売りルール・信頼度）
HORIZONS = {
    "long": {
        "label": "長期（6〜24ヶ月）",
        "recipe": {"value_bp": 0.35, "value_ep": 0.25, "quality_roe": 0.2, "quality_equity_ratio": 0.2},
        "hold_days": 365, "stop_loss": -0.25, "take_profit": None, "rank_exit_q": 0.7,
        "confidence": "高", "basis": "割安(B/P,益回り)＋財務健全(ROE,自己資本比率)。バリュー効果は検証済み。",
    },
    "mid": {
        "label": "中期（1〜3ヶ月）",
        "recipe": {"value_bp": 0.4, "value_ep": 0.4, "reversal_1m": 0.2},
        "hold_days": 90, "stop_loss": -0.15, "take_profit": None, "rank_exit_q": 0.8,
        "confidence": "高", "basis": "value傾斜（本命戦略）。生存者バイアス除去後も対TOPIX +3〜4%/年を確認。",
    },
    "short": {
        "label": "短期（数日〜数週）",
        "recipe": {"reversal_1m": 0.6, "mom_6_1": 0.2, "low_vol": 0.2},
        "hold_days": 20, "stop_loss": -0.08, "take_profit": 0.10, "rank_exit_q": 0.9,
        "confidence": "低（実験的・未検証）", "basis": "短期リバーサル中心。バックテスト未確立のため参考扱い。",
    },
}


def _composite(factors: dict, recipe: dict) -> pd.Series:
    """最新月末で、recipeのファクターをクロスセクション順位合成したスコア。"""
    asof = None
    parts, ws = [], []
    for key, w in recipe.items():
        if key not in factors:
            continue
        panel = factors[key].dropna(how="all")
        if asof is None:
            asof = panel.index[-1]
        parts.append(factors[key].loc[asof].rank(pct=True) * w)
        ws.append(w)
    score = sum(parts) / sum(ws)
    score.attrs["asof"] = asof
    return score


def _recent_vol(prices: pd.DataFrame, code: str, win: int = 20) -> float:
    """直近win営業日の日次リターン年率ボラ（参考情報）。"""
    if code not in prices.columns:
        return float("nan")
    r = np.log(prices[code]).diff().dropna().tail(win)
    return float(r.std() * np.sqrt(252)) if len(r) > 5 else float("nan")


def recommend(horizon: str = "mid", top_n: int = 15) -> dict:
    """指定期間の買い候補と売りルールを返す。"""
    cfg = HORIZONS[horizon]
    prices = load_prices()
    stmt = load_statements()
    membership = load_membership()

    factors = compute_factors(prices)
    factors.update(build_fundamental_factors_jq(prices, stmt))
    score = _composite(factors, cfg["recipe"])
    asof = score.attrs["asof"]

    # 最新月の構成銘柄に限定
    last_ym = membership["month_end"].max().to_period("M")
    members = set(membership.loc[membership["month_end"].dt.to_period("M") == last_ym, "code"])
    name_map = (membership.sort_values("month_end").drop_duplicates("code", keep="last")
                .set_index("code")["name"].to_dict())

    score = score[[c for c in score.index if c in members]].dropna()
    picks = score.sort_values(ascending=False).head(top_n)

    last_price_date = prices.index[-1]
    items = []
    for c in picks.index:
        price = float(prices.loc[last_price_date, c]) if c in prices.columns else float("nan")
        if not np.isfinite(price):
            continue
        items.append({
            "code": c,
            "name": name_map.get(c, ""),
            "score": round(float(picks[c]), 4),
            "price": round(price, 1),
            "recent_vol_pct": round(_recent_vol(prices, c) * 100, 1),
            "stop_loss_price": round(price * (1 + cfg["stop_loss"]), 1),
            "take_profit_price": round(price * (1 + cfg["take_profit"]), 1) if cfg["take_profit"] else None,
        })

    return {
        "horizon": horizon,
        "label": cfg["label"],
        "confidence": cfg["confidence"],
        "basis": cfg["basis"],
        "hold_days": cfg["hold_days"],
        "sell_rules": {
            "stop_loss_pct": cfg["stop_loss"] * 100,
            "take_profit_pct": cfg["take_profit"] * 100 if cfg["take_profit"] else None,
            "rank_exit": f"スコア順位が上位{int((1-cfg['rank_exit_q'])*100)}%圏から外れたら売り",
            "time_stop": f"{cfg['hold_days']}日経過で見直し",
        },
        "asof": str(pd.Timestamp(asof).date()),
        "price_date": str(last_price_date.date()),
        "picks": items,
    }


def recommend_all(top_n: int = 15) -> dict:
    return {h: recommend(h, top_n) for h in HORIZONS}


if __name__ == "__main__":
    for h in HORIZONS:
        r = recommend(h, top_n=8)
        print(f"\n=== {r['label']}  信頼度{r['confidence']} (as of {r['asof']}) ===")
        print(f"  根拠: {r['basis']}")
        print(f"  売り: 損切り{r['sell_rules']['stop_loss_pct']:.0f}% / "
              f"利確{r['sell_rules']['take_profit_pct'] or '—'} / {r['sell_rules']['time_stop']}")
        for it in r["picks"][:8]:
            print(f"   {it['code']} {it['name'][:14]:14} 株価{it['price']:>8.0f} "
                  f"損切り{it['stop_loss_price']:>8.0f} ボラ{it['recent_vol_pct']:.0f}%")
