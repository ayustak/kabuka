"""株価取得（無料: yfinance）。

auto_adjust=True で **配当・分割調整済の終値** を取得する。
取得結果は parquet にキャッシュし、再実行を高速化する。

【注意】yfinanceの調整は配当再投資を反映したトータルリターン系列に近い。
J-Quantsの調整後株価が分割のみ反映なのと異なり、配当影響を含む点はむしろ好都合。
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf

from app.config import DATA_DIR, END_DATE, START_DATE
from app.ingestion.universe import BENCHMARKS, universe_tickers

_PRICE_CACHE = DATA_DIR / "prices_adj_close.parquet"


def fetch_prices(force: bool = False) -> pd.DataFrame:
    """ユニバース＋ベンチマークの調整後終値を取得。

    返り値: index=日付, columns=ティッカー の終値 DataFrame。
    """
    if _PRICE_CACHE.exists() and not force:
        return pd.read_parquet(_PRICE_CACHE)

    tickers = universe_tickers() + list(BENCHMARKS.values())
    raw = yf.download(
        tickers,
        start=START_DATE,
        end=END_DATE,
        auto_adjust=True,   # 配当・分割調整
        progress=False,
        threads=True,
    )
    # 複数銘柄だと列が MultiIndex (Field, Ticker)。Close だけ取り出す。
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    close = close.dropna(how="all")
    # 全期間ほぼ欠損の銘柄（取得失敗）は落とす
    valid = close.columns[close.notna().sum() > len(close) * 0.5]
    close = close[valid]
    close.to_parquet(_PRICE_CACHE)
    return close


if __name__ == "__main__":
    df = fetch_prices(force=True)
    print(f"取得: {df.shape[1]} 銘柄 / {len(df)} 営業日")
    print(f"期間: {df.index.min().date()} 〜 {df.index.max().date()}")
    print(f"キャッシュ: {_PRICE_CACHE}")
