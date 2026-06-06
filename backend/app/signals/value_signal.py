"""value傾斜戦略の売買シグナル生成（小額・NISA・低回転を想定）。

最新月末時点のTOPIX500構成銘柄を value傾斜スコアでランキングし、上位N銘柄を
「買い候補」として返す。各銘柄のB/P・益回り・直近リターン等の根拠も添える。

【設計思想（①の分析を反映）】
- 課税口座の月次全入替は税で優位がほぼ消える → NISA・低回転を推奨。
- よって毎月総入替ではなく「上位リストの提示＋大きな順位変化時のみ入替」を想定。
"""
from __future__ import annotations

import pandas as pd

from app.analysis.backtest.factors import compute_factors
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq
from app.ingestion.jquants_collect import load_membership, load_prices, load_statements


def _value_tilt(factors: dict) -> pd.DataFrame:
    parts, ws = [], []
    for key, w in [("value_bp", 0.4), ("value_ep", 0.4), ("reversal_1m", 0.2)]:
        if key in factors:
            parts.append(factors[key].rank(axis=1, pct=True) * w)
            ws.append(w)
    return sum(parts) / sum(ws)


def generate_value_signal(top_n: int = 30) -> pd.DataFrame:
    """最新月末の買い候補 上位N銘柄を返す。

    返り値カラム: code, name, score, value_bp, value_ep, reversal_1m, weight
    """
    prices = load_prices()
    stmt = load_statements()
    membership = load_membership()

    factors = compute_factors(prices)
    fund = build_fundamental_factors_jq(prices, stmt)
    factors.update(fund)
    vt = _value_tilt(factors)

    # 最新の月末（構成銘柄が存在する直近）
    asof = vt.dropna(how="all").index[-1]

    # 最新月の構成銘柄に限定
    last_ym = membership["month_end"].max().to_period("M")
    members = set(membership.loc[membership["month_end"].dt.to_period("M") == last_ym, "code"])
    name_map = (membership.sort_values("month_end")
                .drop_duplicates("code", keep="last").set_index("code")["name"].to_dict())

    score = vt.loc[asof].dropna()
    score = score[[c for c in score.index if c in members]]
    rank = score.rank(ascending=False)
    picks = rank[rank <= top_n].index

    rows = []
    for c in picks:
        rows.append({
            "code": c,
            "name": name_map.get(c, ""),
            "score": round(float(score[c]), 4),
            "value_bp": round(float(fund["value_bp"].loc[asof, c]), 3) if c in fund["value_bp"].columns else None,
            "value_ep": round(float(fund["value_ep"].loc[asof, c]), 4) if c in fund["value_ep"].columns else None,
            "reversal_1m": round(float(factors["reversal_1m"].loc[asof, c]), 4),
        })
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    df["weight"] = round(1.0 / len(df), 4)  # 等加重
    df.attrs["asof"] = str(asof.date())
    return df


if __name__ == "__main__":
    sig = generate_value_signal(top_n=30)
    print(f"=== value傾斜 買い候補（as of {sig.attrs['asof']}, 等加重 上位{len(sig)}銘柄） ===")
    print(sig.to_string(index=False))
