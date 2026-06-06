"""APIルーター（疎結合の原則: フロントはこのRESTを叩くだけ）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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


@router.get("/recommendations")
def recommendations(horizon: str = "all", top_n: int = 15) -> dict:
    """期間別（short/mid/long/all）の買い候補＋根拠＋売りルール。"""
    from app.signals.recommend import HORIZONS, recommend, recommend_all
    if horizon == "all":
        return recommend_all(top_n)
    if horizon not in HORIZONS:
        raise HTTPException(400, "horizon は short/mid/long/all")
    return recommend(horizon, top_n)


# ---- フォワード・ペーパートレード（メイン機能） ----
class BuyReq(BaseModel):
    code: str
    shares: int
    horizon: str = "mid"
    name: str = ""


class InitReq(BaseModel):
    capital: float = 3_000_000


@router.get("/paper/status")
def paper_status() -> dict:
    from app.signals.portfolio import status
    return status()


@router.post("/paper/init")
def paper_init(req: InitReq) -> dict:
    from app.signals.portfolio import init_portfolio
    init_portfolio(req.capital)
    return {"ok": True, "capital": req.capital}


@router.post("/paper/buy")
def paper_buy(req: BuyReq) -> dict:
    from app.signals.portfolio import buy
    r = buy(req.code, req.shares, req.horizon, req.name)
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


@router.post("/paper/sell")
def paper_sell(code: str, reason: str = "manual") -> dict:
    from app.signals.portfolio import sell
    r = sell(code, reason)
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


@router.get("/tracking/accuracy")
def tracking_accuracy(horizon: str = "all", top_n: int = 15) -> dict:
    """推奨の的中実績（過去データに基づくヒストリカル）。"""
    from app.signals.tracking import HORIZONS, historical_accuracy, historical_accuracy_all
    if horizon == "all":
        return historical_accuracy_all(top_n)
    if horizon not in HORIZONS:
        raise HTTPException(400, "horizon は short/mid/long/all")
    return historical_accuracy(horizon, top_n)


@router.post("/tracking/snapshot")
def tracking_snapshot(top_n: int = 15) -> dict:
    """今日の推奨を記録（実時間フォワード成績の蓄積開始）。"""
    from app.signals.tracking import log_snapshot
    return log_snapshot(top_n)


@router.get("/tracking/live")
def tracking_live() -> dict:
    """記録済みライブ推奨の現時点までの実現リターン。"""
    from app.signals.tracking import live_track_status
    return live_track_status()


@router.get("/simulate")
def simulate_endpoint(
    initial_capital: float = 3_000_000,
    n_holdings: int = 20,
    rebalance: str = "Q",
    account: str = "nisa",
    cost_bps: float = 20.0,
    start: str | None = None,
) -> dict:
    """投資シミュレーション。パラメータを変えて『もしこう投資していたら』を返す。"""
    from app.analysis.simulate import simulate

    if rebalance not in {"M", "Q", "H", "Y"}:
        raise HTTPException(400, "rebalance は M/Q/H/Y のいずれか")
    if account not in {"nisa", "taxable"}:
        raise HTTPException(400, "account は nisa/taxable")
    n_holdings = max(5, min(100, int(n_holdings)))
    try:
        return simulate(initial_capital, n_holdings, rebalance, account, cost_bps, start)
    except FileNotFoundError:
        raise HTTPException(503, "シミュレーター用データ未生成。build_dashboard_data.py を実行してください。")
