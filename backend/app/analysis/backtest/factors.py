"""ファクター計算（MVPは株価のみで作れるものに限定）。

【なぜ株価ファクターから始めるか】
批判役は「日本株ではモメンタムが弱く、value/qualityが効く」と指摘した。value/quality は
財務データ(EDINET等)が要るため後続フェーズに回し、まずは株価だけで作れるファクターで
バックテスト基盤の正しさ（リーク無し・コスト織込み・評価指標）を固める。
同時に「日本株でモメンタムが本当に弱いのか」を自前データで一次確認する狙いもある。

すべてのファクターは「各月末時点で既知の情報のみ」で計算し、未来情報の混入(リーク)を防ぐ。
向きは「スコアが高いほど将来リターンが高いと期待する」に統一する。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _to_month_end(daily_close: pd.DataFrame) -> pd.DataFrame:
    """日次終値を月末値にリサンプル。"""
    return daily_close.resample("ME").last()


def compute_factors(daily_close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """月次パネルのファクター群を返す。

    返り値: {factor_name: DataFrame(index=月末, columns=ticker)}。
    各値は「その月末時点のスコア」。評価時は翌月リターンと突き合わせる。
    """
    me = _to_month_end(daily_close)
    monthly_logret = np.log(me / me.shift(1))  # 月次対数リターン

    factors: dict[str, pd.DataFrame] = {}

    # モメンタム 12-1: 直近12ヶ月のうち最新1ヶ月を除いた累積リターン
    # rolling(12).sum() = 当月含む12ヶ月、shift(1) で最新月を除外 → t-12..t-1
    factors["mom_12_1"] = monthly_logret.rolling(12).sum().shift(1)

    # モメンタム 6-1
    factors["mom_6_1"] = monthly_logret.rolling(6).sum().shift(1)

    # 短期リバーサル: 直近1ヶ月リターンの符号反転（上がった銘柄は翌月下がる傾向の検証）
    factors["reversal_1m"] = -monthly_logret

    # 低ボラティリティ: 過去60営業日の日次リターン実現ボラの符号反転（低ボラ=高スコア）
    daily_logret = np.log(daily_close / daily_close.shift(1))
    vol60 = daily_logret.rolling(60).std()
    factors["low_vol"] = (-vol60).resample("ME").last()

    # インデックスを月末に揃える（low_volのみ別経路のため再整列）
    idx = me.index
    for k in factors:
        factors[k] = factors[k].reindex(idx)

    return factors


def forward_monthly_return(daily_close: pd.DataFrame) -> pd.DataFrame:
    """評価用の「翌月リターン（単純）」。月末tの行に、月t+1のリターンを入れる。"""
    me = _to_month_end(daily_close)
    monthly_simple = me.pct_change()
    return monthly_simple.shift(-1)
