"""バックテストエンジン（クロスセクション分位ポートフォリオ）。

評価設計（批判役の指摘を反映）:
- ルックアヘッド回避: 月末tのファクターで翌月t+1のリターンを取る。重複なしの月次なので
  系列の重なりによるリークは生じない（1〜3ヶ月先の重複ホライズン版は後続でPurged CVを導入）。
- コスト織込み: 分位ポートフォリオの月次入れ替え(turnover)に対し往復コストを必ず控除。
- 評価指標: 月次IC(Spearman) と ICIR、分位ロングショートのスプレッド、年率リターン/ボラ/
  シャープ/最大ドローダウン、勝率。
- 対TOPIX超過: ロングオンリー上位分位の超過リターンも見る（個人のロングオンリー運用の現実）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FactorResult:
    name: str
    ic_mean: float
    ic_ir: float            # 年率換算IR = mean/std * sqrt(12)
    ls_ann_return: float    # ロングショート年率リターン（コスト後）
    ls_sharpe: float
    ls_max_dd: float
    long_ann_excess: float  # ロングオンリー上位分位の対ベンチ年率超過（コスト後）
    long_hit_rate: float    # 上位分位が月次でベンチに勝った割合
    n_months: int


def _max_drawdown(cum: pd.Series) -> float:
    """累積リターン系列(1始まり)の最大ドローダウン（負値）。"""
    peak = cum.cummax()
    dd = cum / peak - 1.0
    return float(dd.min())


def _annualize(monthly_ret: pd.Series) -> tuple[float, float, float]:
    """月次リターン系列から (年率リターン, 年率ボラ, シャープ) を返す。"""
    monthly_ret = monthly_ret.dropna()
    if len(monthly_ret) < 6:
        return float("nan"), float("nan"), float("nan")
    ann_ret = (1 + monthly_ret).prod() ** (12 / len(monthly_ret)) - 1
    ann_vol = monthly_ret.std() * np.sqrt(12)
    sharpe = (monthly_ret.mean() * 12) / ann_vol if ann_vol > 0 else float("nan")
    return float(ann_ret), float(ann_vol), float(sharpe)


def _quantile_returns(
    factor: pd.DataFrame,
    fwd_ret: pd.DataFrame,
    n_q: int,
    cost_bps: float,
) -> dict:
    """分位ポートフォリオの月次リターン（コスト後）と turnover を計算。"""
    cost_oneway = cost_bps / 10000.0
    q_rets: dict[int, list[float]] = {q: [] for q in range(n_q)}
    dates: list[pd.Timestamp] = []
    prev_members: dict[int, set] = {q: set() for q in range(n_q)}
    ics: list[float] = []

    common_idx = factor.index.intersection(fwd_ret.index)
    for dt in common_idx:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt]
        pair = pd.concat([f, r], axis=1, keys=["f", "r"]).dropna()
        if len(pair) < n_q * 3:  # 各分位に最低3銘柄は欲しい
            continue
        # IC（Spearman順位相関）
        ics.append(pair["f"].corr(pair["r"], method="spearman"))
        # 分位割当（0=最低スコア .. n_q-1=最高スコア）
        ranks = pair["f"].rank(method="first")
        labels = pd.qcut(ranks, n_q, labels=False)
        dates.append(dt)
        for q in range(n_q):
            members = set(pair.index[labels == q])
            gross = pair.loc[list(members), "r"].mean()
            # コスト: 入れ替わった割合に往復コスト（売り+買い）
            if prev_members[q]:
                turnover = len(members ^ prev_members[q]) / (2 * max(len(members), 1))
            else:
                turnover = 1.0  # 初月は全建て
            net = gross - turnover * cost_oneway * 2
            q_rets[q].append(net)
            prev_members[q] = members

    return {
        "dates": dates,
        "q_rets": {q: pd.Series(v, index=dates) for q, v in q_rets.items()},
        "ic": pd.Series(ics, index=dates[: len(ics)] if len(ics) == len(dates) else None),
    }


def run_factor(
    name: str,
    factor: pd.DataFrame,
    fwd_ret: pd.DataFrame,
    bench_ret: pd.Series,
    n_q: int,
    cost_bps: float,
) -> tuple[FactorResult, pd.DataFrame]:
    """1ファクターを評価。結果サマリと、分位別月次リターン表を返す。"""
    out = _quantile_returns(factor, fwd_ret, n_q, cost_bps)
    dates = out["dates"]
    q_rets = out["q_rets"]
    ic = out["ic"].dropna()

    top = q_rets[n_q - 1]          # 最高スコア分位（ロング）
    bottom = q_rets[0]             # 最低スコア分位（ショート）
    ls = top - bottom              # ロングショート

    ls_ann, _, ls_sharpe = _annualize(ls)
    ls_dd = _max_drawdown((1 + ls.fillna(0)).cumprod())

    # ロングオンリー上位分位の対ベンチ超過
    b = bench_ret.reindex(top.index)
    excess = (top - b).dropna()
    long_ann_excess, _, _ = _annualize(excess)
    long_hit = float((excess > 0).mean()) if len(excess) else float("nan")

    ic_mean = float(ic.mean()) if len(ic) else float("nan")
    ic_ir = float(ic.mean() / ic.std() * np.sqrt(12)) if ic.std() > 0 else float("nan")

    res = FactorResult(
        name=name,
        ic_mean=ic_mean,
        ic_ir=ic_ir,
        ls_ann_return=ls_ann,
        ls_sharpe=ls_sharpe,
        ls_max_dd=ls_dd,
        long_ann_excess=long_ann_excess,
        long_hit_rate=long_hit,
        n_months=len(dates),
    )
    table = pd.DataFrame({f"Q{q+1}": q_rets[q] for q in range(n_q)})
    table["LS(Q{}-Q1)".format(n_q)] = ls
    return res, table
