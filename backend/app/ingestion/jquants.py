"""J-Quants API v2 クライアント（Standardプラン想定）。

【V2の重要変更】2024年以降、J-Quants は V2 に移行。V1(メール/パスワード→トークン)は廃止。
- 認証: **APIキー方式**（`x-api-key` ヘッダ）。キーに有効期限なし（再発行・削除は可）。
- ベースURL: https://api.jquants.com/v2
- レスポンス: データは "data" 配列。列名が短縮（例 Close→C, Volume→Vo）。
- 主なエンドポイント: 上場銘柄=/equities/master, 株価=/equities/bars/daily, 財務=/fins/summary

【目的】無料スタックの弱点＝生存者バイアスを潰す:
- 時点別の上場銘柄一覧（上場廃止銘柄を含むPITユニバース）
- 分割調整済の株価 / 開示日付きの財務（PIT結合）

【設定】.env に:  KABUKA_JQUANTS_API_KEY=...
（J-Quantsのマイページ＞APIキー で発行）
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import DATA_DIR  # noqa: F401  (.env 読込トリガ)

_BASE = "https://api.jquants.com/v2"

# レート制限対策。Standard=120req/分なので最低間隔0.55秒（≈109/分）に抑える。
_MIN_INTERVAL = float(os.getenv("KABUKA_JQUANTS_MIN_INTERVAL", "0.55"))
_last_call: list[float] = [0.0]


def _throttle() -> None:
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _api_key() -> str:
    key = os.getenv("KABUKA_JQUANTS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "J-Quants APIキーが未設定です。.env に KABUKA_JQUANTS_API_KEY=... を設定してください"
            "（J-Quantsマイページ＞APIキーで発行）。"
        )
    return key


def _get(path: str, params: dict | None = None, max_retry: int = 6) -> dict:
    qs = f"?{urlencode(params)}" if params else ""
    url = f"{_BASE}{path}{qs}"
    for attempt in range(max_retry):
        _throttle()
        req = Request(url, headers={"x-api-key": _api_key()})
        try:
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429 and attempt < max_retry - 1:
                # レート制限。指数バックオフ（1,2,4,8,16秒）で待って再試行。
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("リトライ上限に達しました（429継続）")


def _paginate(path: str, params: dict, data_key: str = "data") -> list[dict]:
    """pagination_key を辿って全件取得。V2はデータが "data" 配列。"""
    out: list[dict] = []
    while True:
        resp = _get(path, params)
        # data_key 優先。互換のため旧キーもフォールバック
        chunk = resp.get(data_key) or next(
            (v for k, v in resp.items() if isinstance(v, list)), []
        )
        out.extend(chunk)
        pk = resp.get("pagination_key")
        if not pk:
            break
        params = {**params, "pagination_key": pk}
    return out


def get_listed_info(date: str | None = None) -> list[dict]:
    """上場銘柄一覧 /equities/master。date指定で時点別（PITユニバース構築用）。"""
    params = {"date": date} if date else {}
    return _paginate("/equities/master", params)


def get_daily_quotes(code: str | None = None, date: str | None = None,
                     from_: str | None = None, to: str | None = None) -> list[dict]:
    """株価四本値 /equities/bars/daily。code指定で1銘柄の期間、date指定で全銘柄の1日分。"""
    params: dict[str, Any] = {}
    if code:
        params["code"] = code
    if date:
        params["date"] = date
    if from_:
        params["from"] = from_
    if to:
        params["to"] = to
    return _paginate("/equities/bars/daily", params)


def get_statements(code: str | None = None, date: str | None = None) -> list[dict]:
    """財務情報 /fins/summary。開示日付きで返るためPIT結合に使える。"""
    params: dict[str, Any] = {}
    if code:
        params["code"] = code
    if date:
        params["date"] = date
    return _paginate("/fins/summary", params)


TOPIX_CODE = "0000"  # J-Quants指数コード: TOPIX


def get_index(code: str = TOPIX_CODE, from_: str | None = None, to: str | None = None) -> list[dict]:
    """指数の四本値 /indices/bars/daily。既定はTOPIX(0000)。"""
    params: dict[str, Any] = {"code": code}
    if from_:
        params["from"] = from_
    if to:
        params["to"] = to
    return _paginate("/indices/bars/daily", params)


if __name__ == "__main__":
    try:
        info = get_listed_info()
        print(f"認証OK。上場銘柄数(現時点): {len(info)}")
        if info:
            # V2で列名が変わっている可能性があるためキー一覧も表示
            print("先頭レコードのキー:", sorted(info[0].keys()))
            print("サンプル:", info[0])
    except (RuntimeError, HTTPError) as e:
        body = e.read().decode()[:200] if isinstance(e, HTTPError) else ""
        print(f"疎通失敗: {e} {body}")
