"""LightGBMでファクターを非線形結合するクロスセクション・モデル（②）。

【設計】
- 特徴量: value_bp, value_ep, quality_roe, quality_margin, quality_equity_ratio,
          mom_12_1, mom_6_1, reversal_1m, low_vol を各月クロスセクションで順位化([0,1])。
- ターゲット: 翌月リターンの各月クロスセクション順位（相対順位の学習＝RankIC最適化に整合）。
- 評価: **Purged K-Fold CV**。月をK個の連続ブロックに分割し、テストブロックの近傍±embargo月を
  訓練から除外（重複ホライズン・先読みリークを防ぐ, López de Prado流）。
  各テストでout-of-fold予測を出し、それを「合成ファクター」として分位ロングショート/IC/DSRで評価。
- 過学習抑制のため木は浅め・min_child_samples大きめ・サブサンプリング。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = [
    "value_bp", "value_ep", "quality_roe", "quality_margin", "quality_equity_ratio",
    "mom_12_1", "mom_6_1", "reversal_1m", "low_vol",
]

_LGB_PARAMS = dict(
    objective="regression",
    n_estimators=300,
    learning_rate=0.03,
    num_leaves=15,          # 浅め
    min_child_samples=50,   # 葉に十分なサンプル
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    n_jobs=-1,
    verbosity=-1,
)


def assemble_panel(factor_panels: dict[str, pd.DataFrame], fwd: pd.DataFrame) -> pd.DataFrame:
    """マスク済みファクター群＋翌月リターンを long 形式に。特徴量とターゲットを各月で順位化。"""
    cols = {f: factor_panels[f].stack() for f in FEATURES if f in factor_panels}
    df = pd.DataFrame(cols)
    df["fwd"] = fwd.stack().reindex(df.index)
    df = df.dropna(subset=["fwd"]).dropna(how="all", subset=list(cols))
    # 各月(level=0)でクロスセクション順位化
    g = df.groupby(level=0)
    for f in cols:
        df[f] = g[f].rank(pct=True)
    df["target"] = df.groupby(level=0)["fwd"].rank(pct=True)
    # 特徴量が全欠損の行は落とす（順位化後の欠損は中央0.5で補完）
    df[list(cols)] = df[list(cols)].fillna(0.5)
    return df


def purged_cv_predict(df: pd.DataFrame, n_splits: int = 5, embargo: int = 1) -> pd.Series:
    """Purged K-Fold（連続ブロック＋エンバーゴ）で out-of-fold 予測を返す。"""
    import lightgbm as lgb

    months = np.array(sorted(df.index.get_level_values(0).unique()))
    pos = {m: i for i, m in enumerate(months)}
    folds = np.array_split(np.arange(len(months)), n_splits)
    feat_cols = [f for f in FEATURES if f in df.columns]
    oof = pd.Series(index=df.index, dtype=float)

    month_of_row = df.index.get_level_values(0).map(pos).to_numpy()

    for fold in folds:
        test_set = set(fold.tolist())
        lo, hi = fold.min(), fold.max()
        # エンバーゴ: テスト範囲±embargo を訓練から除外
        train_ok = np.array([
            (p not in test_set) and (p < lo - embargo or p > hi + embargo)
            for p in range(len(months))
        ])
        train_pos = set(np.where(train_ok)[0].tolist())

        train_mask = np.array([p in train_pos for p in month_of_row])
        test_mask = np.array([p in test_set for p in month_of_row])
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue

        model = lgb.LGBMRegressor(**_LGB_PARAMS)
        model.fit(df.loc[train_mask, feat_cols], df.loc[train_mask, "target"])
        oof.loc[test_mask] = model.predict(df.loc[test_mask, feat_cols])

    return oof


def oof_to_factor(oof: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    """out-of-fold予測(long)を 月末×銘柄 のワイド・ファクターに復元。"""
    wide = oof.unstack()
    return wide.reindex(index=like.index, columns=like.columns)


def feature_importance(df: pd.DataFrame) -> pd.Series:
    """全データで一度学習し、特徴量重要度（gain）を返す（解釈用）。"""
    import lightgbm as lgb

    feat_cols = [f for f in FEATURES if f in df.columns]
    model = lgb.LGBMRegressor(**_LGB_PARAMS)
    model.fit(df[feat_cols], df["target"])
    return pd.Series(model.booster_.feature_importance(importance_type="gain"),
                     index=feat_cols).sort_values(ascending=False)
