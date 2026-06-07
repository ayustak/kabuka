#!/usr/bin/env bash
# kabuka をローカル起動する簡単スクリプト。
# 使い方: ターミナルで ./start.command  もしくは Finder でダブルクリック。
set -e
cd "$(dirname "$0")"

PY=.venv/bin/python
UVICORN=.venv/bin/uvicorn

# 念のため最新データを取得（失敗しても続行）
git pull --rebase --autostash 2>/dev/null || true

cd backend

# 初回などキャッシュ未生成なら作成
if [ ! -f data/strategy_summary.json ]; then
  echo "初回データを準備中…（少し待ちます）"
  ../$PY scripts/build_dashboard_data.py
fi

echo ""
echo "=================================================="
echo "  kabuka を起動します → http://127.0.0.1:8000"
echo "  止めるには Ctrl+C"
echo "=================================================="
echo ""

# 起動直後にブラウザを自動で開く
( sleep 2; open "http://127.0.0.1:8000" ) >/dev/null 2>&1 &

exec ../$UVICORN app.main:app --port 8000
