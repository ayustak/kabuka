"""FastAPI エントリポイント。

設計原則（architecture.md）: バックエンドはAPIとして疎結合。
Web/モバイル/ボットがすべてこのRESTを共有する。
起動: cd backend; ../.venv/bin/uvicorn app.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

app = FastAPI(title="kabuka API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 開発用。本番は限定する。
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
def _startup() -> None:
    # 日次データ更新スケジューラ（KABUKA_ENABLE_SCHEDULER=1 のとき起動）
    from app.scheduler.jobs import maybe_start
    maybe_start()

_STATIC = Path(__file__).resolve().parent / "static"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/about")
def about() -> HTMLResponse:
    """情報収集プロセス・仕組みのやさしい解説（about.md を描画）。"""
    import markdown as md

    text = (_STATIC / "about.md").read_text(encoding="utf-8")
    body = md.markdown(text, extensions=["tables", "fenced_code"])
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>kabuka の仕組み — やさしい解説</title>
<style>
  body {{ font-family:-apple-system,"Hiragino Sans",sans-serif; background:#0f1115; color:#d7dce5;
         max-width:820px; margin:0 auto; padding:24px 20px 80px; line-height:1.8; }}
  a {{ color:#6aa3ff; }} a.back {{ display:inline-block; margin-bottom:16px; }}
  h1 {{ font-size:24px; border-bottom:2px solid #4a7cff; padding-bottom:10px; }}
  h2 {{ font-size:18px; color:#cdd3df; border-left:4px solid #4a7cff; padding-left:10px; margin-top:34px; }}
  blockquote {{ background:#171a21; border-left:3px solid #4a7cff; margin:14px 0; padding:10px 16px; color:#aeb6c5; border-radius:0 8px 8px 0; }}
  table {{ border-collapse:collapse; width:100%; margin:14px 0; font-size:14px; }}
  th,td {{ border:1px solid #2a2f3a; padding:8px 10px; text-align:left; }}
  th {{ background:#171a21; }}
  code {{ background:#1c212b; padding:2px 6px; border-radius:4px; }}
  hr {{ border:none; border-top:1px solid #2a2f3a; margin:28px 0; }}
</style></head><body>
<a class="back" href="/">← ダッシュボードに戻る</a>
{body}
</body></html>"""
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
