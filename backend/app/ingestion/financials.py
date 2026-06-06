"""EDINETから財務データを収集してPITパネルを作る（value/quality用）。

【設計】
EDINET無料APIは「日付ごとの提出書類一覧」しか引けない（銘柄横断検索が無い）ため、
対象期間を日次スキャンし、ユニバースの証券コード(secCode)・有価証券報告書(docTypeCode=120)に
絞って財務CSVを取得・抽出する。提出日(submitDateTime)を保持し、後段でPIT結合する。

【重要】要素ID(elementID)は会社・年度・タクソノミ版で揺れる。下の候補リストは初版であり、
最初の実取得後に実データを見て調整する前提（README/雛形に明記）。

【データ量の現実】10年×全日スキャンは重い。まず `collect_financials(pilot=True)` で
短期間×少数を試し、抽出が正しいか確認してから本収集する運用を推奨。
"""
from __future__ import annotations

import time

import pandas as pd

from app.config import DATA_DIR
from app.ingestion.edinet import DOCTYPE_ANNUAL, fetch_financial_csv, list_documents
from app.ingestion.universe import NIKKEI_CORE

_FIN_CACHE = DATA_DIR / "financials_pit.parquet"

# 抽出したい財務項目 → EDINET要素IDの候補（最初に見つかったものを採用）。
# 「主要な経営指標等(SummaryOfBusinessResults)」は全社共通で当期値が載るため最も堅牢。
# IFRS採用企業を優先し、無ければ日本基準の要素にフォールバックする順序。
FIELD_CANDIDATES: dict[str, list[str]] = {
    "net_sales": [
        "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults",
        "jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults",
        "jpcrp_cor:NetSalesSummaryOfBusinessResults",
        "jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults",
        "jpcrp_cor:OrdinaryIncomeRevenueSummaryOfBusinessResults",
    ],
    "net_income": [
        "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "jpcrp_cor:NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",
        "jpcrp_cor:ProfitLossSummaryOfBusinessResults",
    ],
    "net_assets": [
        "jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "jpcrp_cor:EquityIFRSSummaryOfBusinessResults",
        "jpcrp_cor:NetAssetsSummaryOfBusinessResults",
    ],
    "total_assets": [
        "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
        "jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults",
        "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
    ],
    "equity_ratio_direct": [
        "jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults",
        "jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults",
        "jpcrp_cor:CapitalAdequacyRatioSummaryOfBusinessResults",
    ],
    "shares_outstanding": [
        "jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults",
    ],
}


def _to_float(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return float("nan")


def _extract_fields(csv_map: dict[str, str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for field, candidates in FIELD_CANDIDATES.items():
        val = float("nan")
        for elem in candidates:
            if elem in csv_map:
                val = _to_float(csv_map[elem])
                break
        out[field] = val
    return out


def collect_financials(
    start: str = "2014-01-01",
    end: str = "2024-12-31",
    pilot: bool = False,
    sleep_sec: float = 0.2,
) -> pd.DataFrame:
    """期間内の有価証券報告書を収集し、PIT財務パネルを返す＆キャッシュ保存。

    pilot=True なら直近約30日のみ走査（抽出ロジックの動作確認用）。
    """
    sec_set = set(NIKKEI_CORE)  # 4桁証券コード（EDINETのsecCodeは末尾0付き5桁が多い→前方一致で判定）
    if pilot:
        dates = pd.bdate_range(end=end, periods=20)
    else:
        dates = pd.bdate_range(start=start, end=end)

    records: list[dict] = []
    total = len(dates)
    for i, d in enumerate(dates):
        date_str = d.date().isoformat()
        if i % 100 == 0:
            print(f"  [{i}/{total}] {date_str} … 収集済 {len(records)} 件", flush=True)
        try:
            meta = list_documents(date_str)
        except Exception as e:  # noqa: BLE001
            print(f"  {date_str}: 一覧取得失敗 {e}")
            continue
        for doc in meta.get("results", []):
            if doc.get("docTypeCode") != DOCTYPE_ANNUAL:
                continue
            sec = (doc.get("secCode") or "")[:4]
            if sec not in sec_set:
                continue
            doc_id = doc.get("docID")
            try:
                csv_map = fetch_financial_csv(doc_id)
            except Exception as e:  # noqa: BLE001
                print(f"  {date_str} {sec} {doc_id}: CSV取得失敗 {e}")
                continue
            rec = {
                "sec_code": sec,
                "submit_date": pd.to_datetime(doc.get("submitDateTime")),
                "period_end": pd.to_datetime(doc.get("periodEnd")),
                "doc_id": doc_id,
            }
            rec.update(_extract_fields(csv_map))
            records.append(rec)
            time.sleep(sleep_sec)  # レート制限への配慮

    df = pd.DataFrame(records)
    if not df.empty and not pilot:
        df.to_parquet(_FIN_CACHE)
    return df


def load_financials() -> pd.DataFrame:
    if _FIN_CACHE.exists():
        return pd.read_parquet(_FIN_CACHE)
    raise FileNotFoundError(
        "財務キャッシュが未作成です。EDINETキー設定後 `python -m app.ingestion.financials` を実行してください。"
    )


if __name__ == "__main__":
    print("EDINET財務 パイロット収集（直近20営業日・ユニバース該当の有報のみ）…")
    df = collect_financials(pilot=True)
    if df.empty:
        print("該当書類なし（期間内に対象銘柄の有報提出が無い可能性）。本収集は決算提出期(6月等)を含む期間で。")
    else:
        print(f"取得 {len(df)} 件。抽出サンプル:")
        print(df.head(10).to_string())
