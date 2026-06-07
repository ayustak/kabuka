#!/usr/bin/env bash
# kabuka をローカル起動。最新データで静的ダッシュボード(web/)を配信する。
# 使い方: ターミナルで ./start.command  もしくは Finder でダブルクリック。
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python

# 最新を取得（失敗しても続行）
git pull --rebase --autostash 2>/dev/null || true

echo "データを準備中…（30秒ほど）"
( cd backend && ../$PY scripts/build_dashboard_data.py >/dev/null 2>&1 && ../$PY scripts/export_web.py >/dev/null 2>&1 )

echo ""
echo "=================================================="
echo "  kabuka を起動 → http://127.0.0.1:8000"
echo "  止めるには Ctrl+C"
echo "=================================================="
( sleep 2; open "http://127.0.0.1:8000" ) >/dev/null 2>&1 &

cd web
exec ../$PY -m http.server 8000
