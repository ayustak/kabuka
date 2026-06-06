"""GitHub Pages（静的サイト）用に、必要なデータをJSONへ書き出す。

静的サイトはサーバー計算ができないため、推奨・精度・最新株価・シミュレーション結果(グリッド)を
あらかじめ計算してJSONにしておく。Actionsが日次でこれを実行→Pagesにデプロイする。
出力先: web/data/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from app.signals.recommend import recommend_all
from app.signals.tracking import historical_accuracy_all
from app.analysis.simulate import simulate
from app.ingestion.jquants_collect import load_prices

WEB_DATA = Path(__file__).resolve().parent.parent.parent / "web" / "data"


def main() -> None:
    WEB_DATA.mkdir(parents=True, exist_ok=True)

    def dump(name: str, obj) -> None:
        (WEB_DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  書き出し web/data/{name}")

    # 推奨（全期間）と精度
    dump("recommendations.json", recommend_all(top_n=15))
    dump("accuracy.json", historical_accuracy_all(top_n=15))

    # 既存のサマリ/シグナルもコピー（あれば）
    for src in ("strategy_summary.json", "signals_latest.json"):
        p = Path(__file__).resolve().parent.parent / "data" / src
        if p.exists():
            (WEB_DATA / src).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  コピー web/data/{src}")

    # クライアント側ペーパートレードのマーク用：最新終値（全銘柄）
    px = load_prices()
    last = px.index.max()
    prices_latest = {c: round(float(v), 1) for c, v in px.loc[last].dropna().items()}
    dump("prices_latest.json", {"date": str(last.date()), "prices": prices_latest})

    # シミュレーション結果グリッド（静的サイトはその場計算できないため事前計算）
    grid = []
    for cap in (1_000_000, 3_000_000, 5_000_000, 10_000_000):
        for nh in (10, 20, 30):
            for reb in ("M", "Q", "Y"):
                for acc in ("nisa", "taxable"):
                    r = simulate(cap, nh, reb, acc)
                    if "error" in r:
                        continue
                    grid.append({"key": f"{cap}_{nh}_{reb}_{acc}", **r})
    dump("sim_grid.json", {"scenarios": grid})
    print(f"  シミュレーション {len(grid)} シナリオ")

    # 解説ページ(about.md)を静的HTMLに（Pagesでも /about を見られるように）
    import markdown as _md
    about_md = (Path(__file__).resolve().parent.parent / "app" / "static" / "about.md")
    if about_md.exists():
        body = _md.markdown(about_md.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
        html = ("<!DOCTYPE html><html lang=ja><head><meta charset=UTF-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>kabuka の仕組み</title><link rel=stylesheet href=about.css></head>"
                "<body><a class=back href='index.html'>← ダッシュボードに戻る</a>" + body + "</body></html>")
        (WEB_DATA.parent / "about.html").write_text(html, encoding="utf-8")
        print("  書き出し web/about.html")
    print("完了。web/ にPages用ファイルを出力。")


if __name__ == "__main__":
    main()
