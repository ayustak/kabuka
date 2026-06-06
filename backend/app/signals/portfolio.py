"""フォワード・ペーパートレード（仮想ポートフォリオ）。アプリのメイン機能の中核。

仮想資金で「今」買い、実時間で損益を追跡し、**売りシグナル**（損切り/利確/順位脱落/期間切れ）を
自動判定する。約定・評価は J-Quants の最新終値でマーク（取得不能ならキャッシュにフォールバック）。

永続化: data/paper_portfolio.json
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from app.config import DATA_DIR
from app.signals.recommend import HORIZONS, _composite
from app.analysis.backtest.factors import compute_factors
from app.analysis.backtest.factors_fundamental import build_fundamental_factors_jq

_PATH = DATA_DIR / "paper_portfolio.json"
_COST = 0.002  # 約定コスト片道0.2%


def _today() -> str:
    return dt.date.today().isoformat()


def _load() -> dict:
    if _PATH.exists():
        return json.loads(_PATH.read_text(encoding="utf-8"))
    return {"initial_capital": 0, "cash": 0, "positions": [], "history": [], "created_at": None}


def _save(p: dict) -> None:
    _PATH.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def init_portfolio(capital: float = 3_000_000) -> dict:
    p = {"initial_capital": capital, "cash": float(capital),
         "positions": [], "history": [], "created_at": _today()}
    _save(p)
    return p


def latest_price(code: str) -> float:
    """最新終値。J-Quantsライブ優先、失敗時はキャッシュの最終値。"""
    try:
        from app.ingestion.jquants import get_daily_quotes
        q = get_daily_quotes(code=code)
        adj = [r.get("AdjC") for r in q if r.get("AdjC") is not None]
        if adj:
            return float(adj[-1])
    except Exception:  # noqa: BLE001
        pass
    try:
        px = pd.read_parquet(DATA_DIR / "jq_prices.parquet")
        if code in px.columns:
            return float(px[code].dropna().iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    return float("nan")


def buy(code: str, shares: int, horizon: str = "mid", name: str = "") -> dict:
    p = _load()
    cfg = HORIZONS.get(horizon, HORIZONS["mid"])
    price = latest_price(code)
    if not np.isfinite(price):
        return {"error": f"{code} の価格取得に失敗"}
    cost = price * shares * (1 + _COST)
    if cost > p["cash"]:
        return {"error": f"資金不足（必要 {cost:,.0f}円 / 残 {p['cash']:,.0f}円）"}
    p["cash"] -= cost
    p["positions"].append({
        "code": code, "name": name, "shares": int(shares), "horizon": horizon,
        "entry_price": round(price, 1), "entry_date": _today(),
        "stop_loss_price": round(price * (1 + cfg["stop_loss"]), 1),
        "take_profit_price": round(price * (1 + cfg["take_profit"]), 1) if cfg["take_profit"] else None,
        "hold_days": cfg["hold_days"],
    })
    _save(p)
    return {"ok": True, "bought": {"code": code, "shares": shares, "price": price}}


def sell(code: str, reason: str = "manual") -> dict:
    p = _load()
    pos = next((x for x in p["positions"] if x["code"] == code), None)
    if not pos:
        return {"error": f"{code} は保有していません"}
    price = latest_price(code)
    proceeds = price * pos["shares"] * (1 - _COST)
    pnl = proceeds - pos["entry_price"] * pos["shares"]
    p["cash"] += proceeds
    p["positions"] = [x for x in p["positions"] if x["code"] != code]
    p["history"].append({**pos, "exit_price": round(price, 1), "exit_date": _today(),
                         "reason": reason, "pnl": round(pnl)})
    _save(p)
    return {"ok": True, "sold": {"code": code, "price": price, "pnl": round(pnl), "reason": reason}}


def _days_held(entry_date: str) -> int:
    return (dt.date.fromisoformat(_today()) - dt.date.fromisoformat(entry_date)).days


def _current_scores() -> dict:
    """期間別の現在スコア（順位脱落判定用）。"""
    px = pd.read_parquet(DATA_DIR / "jq_prices.parquet")
    stmt = pd.read_parquet(DATA_DIR / "jq_statements.parquet")
    factors = compute_factors(px)
    factors.update(build_fundamental_factors_jq(px, stmt))
    return {h: _composite(factors, cfg["recipe"]) for h, cfg in HORIZONS.items()}


def status() -> dict:
    """保有ポジションの現在損益と、各ポジションの売りシグナルを返す。"""
    p = _load()
    scores = _current_scores() if p["positions"] else {}
    rows, mkt_val = [], 0.0
    for pos in p["positions"]:
        cur = latest_price(pos["code"])
        val = cur * pos["shares"]
        mkt_val += val
        pnl = (cur - pos["entry_price"]) * pos["shares"]
        pnl_pct = (cur / pos["entry_price"] - 1) * 100
        held = _days_held(pos["entry_date"])
        cfg = HORIZONS[pos["horizon"]]

        # 売りシグナル判定（透明なルール）
        signals = []
        if pos.get("stop_loss_price") and cur <= pos["stop_loss_price"]:
            signals.append("損切り（ストップ到達）")
        if pos.get("take_profit_price") and cur >= pos["take_profit_price"]:
            signals.append("利確（目標到達）")
        if held >= cfg["hold_days"]:
            signals.append(f"期間切れ（{cfg['hold_days']}日経過・要見直し）")
        sc = scores.get(pos["horizon"])
        if sc is not None and pos["code"] in sc.index:
            rank = sc.rank(pct=True)[pos["code"]]
            if rank < cfg["rank_exit_q"]:
                signals.append("順位脱落（上位圏から外れた）")

        rows.append({
            "code": pos["code"], "name": pos["name"], "horizon": pos["horizon"],
            "shares": pos["shares"], "entry_price": pos["entry_price"], "current_price": round(cur, 1),
            "pnl": round(pnl), "pnl_pct": round(pnl_pct, 1), "days_held": held,
            "stop_loss_price": pos.get("stop_loss_price"),
            "sell_signals": signals, "action": "売り検討" if signals else "保有継続",
        })

    total = p["cash"] + mkt_val
    return {
        "initial_capital": p["initial_capital"], "cash": round(p["cash"]),
        "market_value": round(mkt_val), "total_value": round(total),
        "total_pnl": round(total - p["initial_capital"]) if p["initial_capital"] else 0,
        "total_pnl_pct": round((total / p["initial_capital"] - 1) * 100, 1) if p["initial_capital"] else 0,
        "positions": rows, "closed": p["history"][-20:], "created_at": p["created_at"],
    }


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2)[:800])
