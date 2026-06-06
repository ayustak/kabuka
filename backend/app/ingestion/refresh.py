"""データの増分リフレッシュ（フォワード・ペーパートレード精度の土台）。

推奨は最新データで計算されるべき。ここでは既存キャッシュに「新しい日付分だけ」を追加する:
- universe_membership: 未取得の月末（TOPIX500構成）を追記
- jq_prices: 各銘柄の最終取得日以降を追記、新規銘柄は全期間取得
- jq_statements: 全銘柄の連結FYを再取得して置換（1銘柄あたり軽量）
- topix_index: 最終日以降を追記

実行: cd backend; ../.venv/bin/python -m app.ingestion.refresh
日次自動化は app/scheduler/jobs.py（APScheduler）。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.config import DATA_DIR, START_DATE
from app.ingestion.jquants import TOPIX_CODE, get_daily_quotes, get_index, get_listed_info, get_statements
from app.ingestion.jquants_collect import (
    TOPIX500_SCALE, _STMT, _UNIV, _PRICES, _f, collect_statements,
)

_TOPIX_CACHE = DATA_DIR / "topix_index.parquet"


def _today() -> str:
    return dt.date.today().isoformat()


def update_universe() -> pd.DataFrame:
    univ = pd.read_parquet(_UNIV) if _UNIV.exists() else pd.DataFrame(columns=["month_end", "code", "s33", "name"])
    last = univ["month_end"].max() if len(univ) else pd.Timestamp(START_DATE)
    new_months = pd.date_range(start=last + pd.offsets.BMonthEnd(), end=_today(), freq="BME")
    rows = []
    for me in new_months:
        try:
            info = get_listed_info(date=me.date().isoformat())
        except Exception as e:  # noqa: BLE001
            print(f"  universe {me.date()} 失敗 {e}")
            continue
        for r in info:
            if r.get("ScaleCat") in TOPIX500_SCALE:
                rows.append({"month_end": me, "code": r["Code"], "s33": r.get("S33"), "name": r.get("CoName")})
    if rows:
        univ = pd.concat([univ, pd.DataFrame(rows)], ignore_index=True)
        univ.to_parquet(_UNIV)
        print(f"  universe: +{len(new_months)}ヶ月 / 計{univ['month_end'].nunique()}ヶ月")
    else:
        print("  universe: 追加なし（最新）")
    return univ


def update_prices(codes: list[str]) -> pd.DataFrame:
    existing = pd.read_parquet(_PRICES) if _PRICES.exists() else pd.DataFrame()
    last_date = existing.index.max() if len(existing) else pd.Timestamp(START_DATE)
    frm = (last_date + pd.Timedelta(days=1)).date().isoformat()
    series = {}
    for i, code in enumerate(codes):
        if i % 100 == 0:
            print(f"  prices {i}/{len(codes)}", flush=True)
        start = frm if (len(existing) and code in existing.columns) else "2016-06-06"
        try:
            q = get_daily_quotes(code=code, from_=start, to=_today())
        except Exception as e:  # noqa: BLE001
            print(f"    {code} 失敗 {e}")
            continue
        if q:
            series[code] = pd.Series({pd.to_datetime(r["Date"]): r.get("AdjC")
                                      for r in q if r.get("AdjC") is not None})
    if series:
        new_wide = pd.DataFrame(series)
        combined = existing.combine_first(new_wide) if len(existing) else new_wide
        combined = combined.sort_index()
        combined.to_parquet(_PRICES)
        print(f"  prices: 〜{combined.index.max().date()} / {combined.shape[1]}銘柄")
        return combined
    print("  prices: 追加なし")
    return existing


def update_statements(codes: list[str]) -> pd.DataFrame:
    """連結FYを再取得して置換（per-code軽量・重複や訂正に強い）。"""
    return collect_statements(codes)


def update_topix() -> None:
    existing = pd.read_parquet(_TOPIX_CACHE)["C"] if _TOPIX_CACHE.exists() else pd.Series(dtype=float)
    last = existing.index.max() if len(existing) else pd.Timestamp("2016-06-06")
    frm = (last + pd.Timedelta(days=1)).date().isoformat()
    rows = get_index(TOPIX_CODE, from_=frm, to=_today())
    if rows:
        new = pd.Series({pd.to_datetime(r["Date"]): r["C"] for r in rows})
        combined = pd.concat([existing, new])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_frame("C").to_parquet(_TOPIX_CACHE)
        print(f"  topix: 〜{combined.index.max().date()}")


def refresh_all() -> None:
    print(f"=== データ・リフレッシュ開始 {_today()} ===")
    univ = update_universe()
    codes = sorted(univ["code"].unique())
    print(f"対象銘柄 {len(codes)}")
    print("--- 株価 ---"); update_prices(codes)
    print("--- 財務 ---"); update_statements(codes)
    print("--- TOPIX ---"); update_topix()
    print("=== 完了。次に build_dashboard_data.py でキャッシュ再生成 ===")


if __name__ == "__main__":
    refresh_all()
