"""統計的有意性の評価（批判役の最重要指摘「αが偶然でないか」への対処）。

複数のファクター/戦略を試すと、偶然うまくいったものが必ず出る（多重検定・p-hacking）。
これを補正するのが Deflated Sharpe Ratio (Bailey & López de Prado, 2014)。
- PSR: 観測シャープが基準シャープ SR* を上回る確率（リターンの歪度・尖度も考慮）。
- DSR: SR* を「N回試行したなら偶然これくらいは出る」水準に設定した PSR。
  DSR が高い（例 >0.95）ほど、そのシャープは多重検定を補正しても本物らしい。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


def probabilistic_sharpe_ratio(
    returns: pd.Series, sr_benchmark: float = 0.0
) -> float:
    """PSR: 観測シャープ(per-period)が sr_benchmark を上回る確率。

    returns: 期間リターン系列（ここでは月次）。sr_benchmark も per-period 単位。
    """
    r = returns.dropna()
    n = len(r)
    if n < 6 or r.std() == 0:
        return float("nan")
    sr = r.mean() / r.std()              # per-period シャープ
    skew = r.skew()
    kurt = r.kurt() + 3.0                # pandasは過剰尖度なので+3で通常の尖度に
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    z = (sr - sr_benchmark) * np.sqrt(n - 1) / denom
    return float(norm.cdf(z))


def deflated_sharpe_ratio(
    returns: pd.Series, all_trial_sharpes: list[float], n_trials: int | None = None
) -> dict:
    """DSR: 多重検定を補正したうえで観測シャープが本物らしい確率。

    returns: 評価対象戦略の期間リターン（月次）。
    all_trial_sharpes: 試した全戦略の per-period シャープのリスト（試行のばらつき推定用）。
    n_trials: 試行回数（既定は all_trial_sharpes の数）。
    """
    sharpes = pd.Series([s for s in all_trial_sharpes if np.isfinite(s)])
    N = n_trials or len(sharpes)
    var_sr = sharpes.var(ddof=1) if len(sharpes) > 1 else 0.0
    if var_sr <= 0 or N < 2:
        # 試行が1つなら多重検定補正できない → 素のPSR(対0)を返す
        return {"sr_star": 0.0, "dsr": probabilistic_sharpe_ratio(returns, 0.0), "n_trials": N}
    # 期待される最大シャープ SR* (試行N回の極値)
    sr_star = np.sqrt(var_sr) * (
        (1 - EULER_MASCHERONI) * norm.ppf(1 - 1.0 / N)
        + EULER_MASCHERONI * norm.ppf(1 - 1.0 / (N * np.e))
    )
    return {
        "sr_star": float(sr_star),
        "dsr": probabilistic_sharpe_ratio(returns, sr_star),
        "n_trials": N,
    }
