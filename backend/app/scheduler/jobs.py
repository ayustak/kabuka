"""日次データ更新ジョブ（APScheduler）。推奨を常に最新データで計算するための土台。

J-Quantsは引け後16:30〜財務18:00頃更新のため、日次ジョブは平日 18:30(JST) に実行する。
FastAPI起動時に環境変数 KABUKA_ENABLE_SCHEDULER=1 のとき自動起動（既定は無効＝手動運用）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

_BACKEND = Path(__file__).resolve().parent.parent.parent


def daily_refresh() -> None:
    """日次パイプライン: データ増分更新 → キャッシュ再生成 → 推奨スナップショット記録。"""
    from app.ingestion.refresh import refresh_all
    from app.signals.tracking import log_snapshot

    print("[scheduler] 日次パイプライン開始")
    refresh_all()
    # キャッシュ再生成（別プロセスで安全に）
    subprocess.run([sys.executable, str(_BACKEND / "scripts" / "build_dashboard_data.py")],
                   cwd=str(_BACKEND), check=False)
    # フォワード成績のための推奨スナップショットを記録
    try:
        r = log_snapshot()
        print(f"[scheduler] スナップショット記録: {r}")
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] スナップショット記録失敗: {e}")
    print("[scheduler] 日次パイプライン完了")


def start_scheduler() -> BackgroundScheduler | None:
    """平日18:30(JST)に日次更新をスケジュール。"""
    sched = BackgroundScheduler(timezone="Asia/Tokyo")
    sched.add_job(daily_refresh, "cron", day_of_week="mon-fri", hour=18, minute=30,
                  id="daily_refresh", misfire_grace_time=3600)
    sched.start()
    print("[scheduler] 起動: 平日18:30(JST)に日次リフレッシュ")
    return sched


def maybe_start() -> BackgroundScheduler | None:
    if os.getenv("KABUKA_ENABLE_SCHEDULER") == "1":
        return start_scheduler()
    print("[scheduler] 無効（KABUKA_ENABLE_SCHEDULER=1 で有効化）")
    return None


if __name__ == "__main__":
    # cron 等から1回だけ日次パイプラインを実行する用途
    daily_refresh()
