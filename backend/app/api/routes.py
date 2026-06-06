"""APIルーター（疎結合の原則: フロントはこのRESTを叩くだけ）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _read_json(name: str) -> dict:
    path = DATA_DIR / name
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"{name} 未生成。`python scripts/build_dashboard_data.py` を実行してください。",
        )
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/strategy/summary")
def strategy_summary() -> dict:
    """バックテスト戦略サマリ（事前計算キャッシュ）。"""
    return _read_json("strategy_summary.json")


@router.get("/signals/latest")
def signals_latest() -> dict:
    """最新の買い候補（事前計算キャッシュ）。"""
    return _read_json("signals_latest.json")
