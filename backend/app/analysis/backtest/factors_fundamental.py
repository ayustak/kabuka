"""value/quality ファクター（EDINET財務 × 株価）。

【PIT結合】各月末tでは「提出日(submit_date) <= t」の最新決算のみを使う（先読み防止）。
これにより開示ラグを正しく織り込み、決算期末日基準の先読みリーク（批判役の指摘）を避ける。

向きは「スコアが高いほど将来リターンが高いと期待」に統一:
- value : B/P, E/P が高い（割安）ほど高スコア
- quality: ROE, 営業利益率, 自己資本比率 が高いほど高スコア
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _sec_to_ticker(sec: str) -> str:
    return f"{sec}.T"


def build_fundamental_factors(
    daily_close: pd.DataFrame, fin: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """月末パネルの value/quality ファクター群を返す。

    daily_close: 日次調整後終値（columns=ticker）。
    fin: financials.collect_financials() のPITパネル。
    """
    me = daily_close.resample("ME").last()
    month_ends = me.index

    # sec_code -> ticker、価格に存在するものだけ対象
    fin = fin.copy()
    fin["ticker"] = fin["sec_code"].map(_sec_to_ticker)
    fin = fin[fin["ticker"].isin(me.columns)]
    fin = fin.dropna(subset=["submit_date"]).sort_values("submit_date")

    # 各ティッカー×各月末で、提出済み最新決算をasof結合
    factor_names = ["value_bp", "value_ep", "quality_roe", "quality_margin", "quality_equity_ratio"]
    panels = {name: pd.DataFrame(index=month_ends, columns=me.columns, dtype=float) for name in factor_names}

    for ticker, g in fin.groupby("ticker"):
        g = g.sort_values("submit_date")
        # 各月末に対し submit_date <= 月末 の最新行を取る
        idx = g["submit_date"].searchsorted(month_ends, side="right") - 1
        for i, t in enumerate(month_ends):
            j = idx[i]
            if j < 0:
                continue
            row = g.iloc[j]
            price = me.at[t, ticker]
            if not np.isfinite(price):
                continue
            shares = row.get("shares_outstanding", np.nan)
            mktcap = price * shares if np.isfinite(shares) and shares > 0 else np.nan

            na = row.get("net_assets", np.nan)
            ni = row.get("net_income", np.nan)
            sales = row.get("net_sales", np.nan)
            ta = row.get("total_assets", np.nan)
            eq_ratio = row.get("equity_ratio_direct", np.nan)

            if np.isfinite(mktcap) and mktcap > 0:
                panels["value_bp"].at[t, ticker] = na / mktcap if np.isfinite(na) else np.nan
                panels["value_ep"].at[t, ticker] = ni / mktcap if np.isfinite(ni) else np.nan
            # ROE = 純利益 / 自己資本
            panels["quality_roe"].at[t, ticker] = ni / na if np.isfinite(ni) and np.isfinite(na) and na != 0 else np.nan
            # 純利益率 = 純利益 / 売上（営業利益率は主要指標表に無い企業が多いため純利益率を採用）
            panels["quality_margin"].at[t, ticker] = ni / sales if np.isfinite(ni) and np.isfinite(sales) and sales != 0 else np.nan
            # 自己資本比率: 開示の直接値を優先、無ければ 自己資本/総資産
            if np.isfinite(eq_ratio):
                panels["quality_equity_ratio"].at[t, ticker] = eq_ratio
            elif np.isfinite(na) and np.isfinite(ta) and ta != 0:
                panels["quality_equity_ratio"].at[t, ticker] = na / ta

    return panels


def build_fundamental_factors_jq(
    daily_close: pd.DataFrame, stmt: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """J-Quants /fins/summary スキーマから value/quality を構築（PIT: disc_date<=月末）。

    daily_close: J-Quants調整後終値（columns=5桁code）。
    stmt: jquants_collect.collect_statements() の連結FYパネル
          （code, disc_date, net_income, equity, sales, total_assets, equity_ratio, bps, shares_fy）。
    """
    me = daily_close.resample("ME").last()
    month_ends = me.index
    stmt = stmt.dropna(subset=["disc_date"]).sort_values("disc_date")

    names = ["value_bp", "value_ep", "quality_roe", "quality_margin", "quality_equity_ratio"]
    panels = {n: pd.DataFrame(index=month_ends, columns=me.columns, dtype=float) for n in names}

    for code, g in stmt.groupby("code"):
        if code not in me.columns:
            continue
        g = g.sort_values("disc_date")
        idx = g["disc_date"].searchsorted(month_ends, side="right") - 1
        for i, t in enumerate(month_ends):
            j = idx[i]
            if j < 0:
                continue
            row = g.iloc[j]
            price = me.at[t, code]
            if not np.isfinite(price) or price <= 0:
                continue
            bps, ni, eq = row.get("bps"), row.get("net_income"), row.get("equity")
            sales, shares, eqar = row.get("sales"), row.get("shares_fy"), row.get("equity_ratio")
            if np.isfinite(bps):
                panels["value_bp"].at[t, code] = bps / price            # B/P = BPS/株価
            if np.isfinite(ni) and np.isfinite(shares) and shares > 0:
                panels["value_ep"].at[t, code] = (ni / shares) / price  # E/P = EPS/株価
            if np.isfinite(ni) and np.isfinite(eq) and eq != 0:
                panels["quality_roe"].at[t, code] = ni / eq
            if np.isfinite(ni) and np.isfinite(sales) and sales != 0:
                panels["quality_margin"].at[t, code] = ni / sales
            if np.isfinite(eqar):
                panels["quality_equity_ratio"].at[t, code] = eqar
    return panels


def build_value_quality_composite(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """value系・quality系をクロスセクション順位平均で合成した総合スコア。"""
    parts = [p.rank(axis=1, pct=True) for p in panels.values()]
    return sum(parts) / len(parts)
