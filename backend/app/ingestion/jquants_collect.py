"""J-Quants v2 から生存者バイアス除去版の検証データを収集する。

【何を集めるか】
1. PITユニバース: 各月末時点の TOPIX500 構成銘柄（ScaleCat=Core30/Large70/Mid400）。
   時点別に取るので、当時上場し後に廃止された銘柄も自然に含まれ、生存者バイアスを排除。
2. 株価: 上記ユニオン銘柄の調整後終値(AdjC)。廃止銘柄は廃止日まで取得できる。
3. 財務: /fins/summary を DiscDate(開示日) 付きで取得 → PIT結合に使う。

【出力(parquet)】
- universe_membership.parquet : (month_end, code) ロング形式
- jq_prices.parquet           : index=日付, columns=code の調整後終値
- jq_statements.parquet       : 連結・通期(FY)の主要財務 + 開示日
"""
from __future__ import annotations

import time

import pandas as pd

from app.config import DATA_DIR, END_DATE, START_DATE
from app.ingestion.jquants import get_daily_quotes, get_listed_info, get_statements

# TOPIX500 = Core30 + Large70 + Mid400
TOPIX500_SCALE = {"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"}

_UNIV = DATA_DIR / "universe_membership.parquet"
_PRICES = DATA_DIR / "jq_prices.parquet"
_STMT = DATA_DIR / "jq_statements.parquet"


def collect_universe(start: str = START_DATE, end: str = END_DATE) -> pd.DataFrame:
    """各月末のTOPIX500構成銘柄を収集 → (month_end, code) ロング形式。"""
    month_ends = pd.date_range(start=start, end=end, freq="BME")  # 各月の最終営業日
    rows = []
    for i, me in enumerate(month_ends):
        ds = me.date().isoformat()
        if i % 12 == 0:
            print(f"  [universe {i}/{len(month_ends)}] {ds}", flush=True)
        try:
            info = get_listed_info(date=ds)
        except Exception as e:  # noqa: BLE001
            print(f"    {ds} 取得失敗 {e}")
            continue
        for r in info:
            if r.get("ScaleCat") in TOPIX500_SCALE:
                rows.append({"month_end": me, "code": r["Code"], "s33": r.get("S33"), "name": r.get("CoName")})
    df = pd.DataFrame(rows)
    df.to_parquet(_UNIV)
    return df


def collect_prices(codes: list[str], start: str = START_DATE, end: str = END_DATE) -> pd.DataFrame:
    """銘柄ごとに調整後終値(AdjC)を取得 → wide パネル。"""
    series = {}
    for i, code in enumerate(codes):
        if i % 50 == 0:
            print(f"  [prices {i}/{len(codes)}] {code}", flush=True)
        try:
            q = get_daily_quotes(code=code, from_=start, to=end)
        except Exception as e:  # noqa: BLE001
            print(f"    {code} 取得失敗 {e}")
            continue
        if not q:
            continue
        s = pd.Series(
            {pd.to_datetime(r["Date"]): r.get("AdjC") for r in q if r.get("AdjC") is not None}
        )
        series[code] = s
        time.sleep(0.05)
    prices = pd.DataFrame(series).sort_index()
    prices.to_parquet(_PRICES)
    return prices


def collect_statements(codes: list[str]) -> pd.DataFrame:
    """銘柄ごとに財務を取得。連結・通期(FY)のみ、主要科目＋開示日を保持。"""
    rows = []
    for i, code in enumerate(codes):
        if i % 50 == 0:
            print(f"  [stmt {i}/{len(codes)}] {code}", flush=True)
        try:
            s = get_statements(code=code)
        except Exception as e:  # noqa: BLE001
            print(f"    {code} 取得失敗 {e}")
            continue
        for r in s:
            if r.get("CurPerType") != "FY":
                continue
            if "Consolidated" not in (r.get("DocType") or ""):
                continue
            rows.append({
                "code": code,
                "disc_date": pd.to_datetime(r.get("DiscDate")),
                "fy_end": pd.to_datetime(r.get("CurFYEn")),
                "sales": _f(r.get("Sales")),
                "op": _f(r.get("OP")),
                "net_income": _f(r.get("NP")),
                "equity": _f(r.get("Eq")),
                "total_assets": _f(r.get("TA")),
                "equity_ratio": _f(r.get("EqAR")),
                "bps": _f(r.get("BPS")),
                "shares_fy": _f(r.get("ShOutFY")),
                "treasury_fy": _f(r.get("TrShFY")),
            })
        time.sleep(0.05)
    df = pd.DataFrame(rows).dropna(subset=["disc_date"]).sort_values("disc_date")
    df.to_parquet(_STMT)
    return df


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def load_membership() -> pd.DataFrame:
    return pd.read_parquet(_UNIV)


def load_prices() -> pd.DataFrame:
    return pd.read_parquet(_PRICES)


def load_statements() -> pd.DataFrame:
    return pd.read_parquet(_STMT)


def collect_all(start: str = START_DATE, end: str = END_DATE) -> None:
    print("=== (1/3) PITユニバース（TOPIX500・時点別） ===")
    univ = collect_universe(start, end)
    codes = sorted(univ["code"].unique())
    print(f"ユニオン銘柄数（期間中に一度でもTOPIX500入り）: {len(codes)}")
    print("=== (2/3) 株価（調整後終値） ===")
    collect_prices(codes, start, end)
    print("=== (3/3) 財務（連結FY・開示日付き） ===")
    collect_statements(codes)
    print("完了。data/ に universe_membership / jq_prices / jq_statements を保存。")


if __name__ == "__main__":
    collect_all()
