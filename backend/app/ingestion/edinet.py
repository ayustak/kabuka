"""EDINET API v2 クライアント（無料・公式。value/quality特徴量の履歴財務取得用）。

【ステータス】雛形。実行には EDINET の **無料APIキー** が必要。
  1. https://disclosure2.edinet-fsa.go.jp/ でアカウント作成→APIキー発行（無料）
  2. `.env` に `KABUKA_EDINET_KEY=...` を設定

【なぜEDINETか】
yfinanceのファンダメンタルズは「現時点スナップショット」のみで履歴が無く、過去バックテストに
使うと重大なルックアヘッド（非PIT）になる。EDINETは有価証券報告書/四半期報告をXBRLで配信し、
**提出日(submitDateTime)** が分かるため開示ラグを正しく織り込める＝PITに近い財務が組める。

【次の実装ステップ】
- list_documents() で対象日の提出書類一覧を取得（type=2: 有報・四半期等のメタ）
- get_document() でZIP(XBRL)を取得 → XBRL/iXBRLをパースして
  売上・営業利益・純利益・自己資本・総資産等を抽出（→ ROE/ROA/利益率/B/P/E/P を構築）
- 提出日基準でDBに格納し、factors_fundamental.py で value/quality ファクター化
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from app.config import DATA_DIR

_BASE = "https://api.edinet-fsa.go.jp/api/v2"


def _api_key() -> str:
    key = os.getenv("KABUKA_EDINET_KEY", "")
    if not key:
        raise RuntimeError(
            "EDINET APIキーが未設定です。無料登録後、.env に KABUKA_EDINET_KEY=... を設定してください。"
        )
    return key


def list_documents(date: str) -> dict:
    """指定日(YYYY-MM-DD)の提出書類一覧(メタ)を取得。type=2 はメタ情報付き。"""
    params = urlencode({"date": date, "type": 2, "Subscription-Key": _api_key()})
    req = Request(f"{_BASE}/documents.json?{params}")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 書類種別コード（財務の主軸）
DOCTYPE_ANNUAL = "120"      # 有価証券報告書（年次）
DOCTYPE_QUARTERLY = "140"   # 四半期報告書
DOCTYPE_SEMIANNUAL = "160"  # 半期報告書


def get_document(doc_id: str, doc_type: int = 5) -> bytes:
    """書類本体を取得。

    doc_type=1: 提出本文書&XBRL(ZIP) / doc_type=5: XBRLをCSV化したZIP（パースが容易で堅牢）。
    財務数値の抽出には CSV(=5) を推奨。
    """
    params = urlencode({"type": doc_type, "Subscription-Key": _api_key()})
    req = Request(f"{_BASE}/documents/{doc_id}?{params}")
    with urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_financial_csv(doc_id: str) -> dict[str, str]:
    """書類のCSV(type=5)を取得し、{要素ID: 値} の辞書にして返す。

    EDINETのCSVは UTF-16 / タブ区切りで、列は
    [要素ID, 項目名, コンテキストID, 相対年度, 連結・個別, 期間・時点, ユニットID, 単位, 値]。
    同一要素IDが複数コンテキストで出るため「当期・連結」を優先採用する。
    """
    import io
    import zipfile

    raw = get_document(doc_id, doc_type=5)
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for fname in zf.namelist():
            if not fname.lower().endswith(".csv"):
                continue
            text = zf.read(fname).decode("utf-16", errors="replace")
            for line in text.splitlines()[1:]:  # 先頭はヘッダ
                # 各カラムはダブルクォート囲み → 分割後にクォートを除去
                cols = [c.strip().strip('"') for c in line.split("\t")]
                if len(cols) < 9:
                    continue
                elem, _name, ctx, _yr, _cons, _pt, _uid, _unit, val = cols[:9]
                # 当期(CurrentYear)かつ連結（個別=コンテキストの _NonConsolidatedMember を除外）を優先。
                # 連結/個別の区別は「連結・個別」列ではなくコンテキストIDに入る点に注意。
                prefer = ("CurrentYear" in ctx) and ("NonConsolidated" not in ctx)
                if not elem:
                    continue
                if prefer:                 # 連結・当期は常に採用（優先）
                    out[elem] = val
                elif elem not in out:      # 未取得なら暫定で非優先値を入れておく
                    out[elem] = val
    return out


def save_document(doc_id: str, dest_dir: Path | None = None) -> Path:
    """書類ZIPをデータディレクトリに保存し、保存先パスを返す。"""
    dest_dir = dest_dir or (DATA_DIR / "edinet")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{doc_id}.zip"
    path.write_bytes(get_document(doc_id))
    return path


if __name__ == "__main__":
    # キーがあれば直近営業日の件数を表示するだけの疎通確認
    import datetime as _dt

    today = _dt.date.today().isoformat()
    try:
        data = list_documents(today)
        print(f"{today} の提出書類: {data.get('metadata', {}).get('resultset', {}).get('count')} 件")
    except Exception as e:  # noqa: BLE001
        print(f"疎通確認スキップ: {e}")
