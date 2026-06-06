"""FastAPI エントリポイント。

設計原則（architecture.md）: バックエンドはAPIとして疎結合。
Web/モバイル/ボットがすべてこのRESTを共有する。
起動: cd backend; ../.venv/bin/uvicorn app.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

_STATIC = Path(__file__).resolve().parent / "static"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
