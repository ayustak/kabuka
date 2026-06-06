"""設定（環境変数で上書き可）。

architecture.md の方針どおり、設定はすべて環境変数に外部化する。
当面は手元Macで都度実行するため、デフォルト値だけで動くようにしてある。
"""
from __future__ import annotations

import os
from pathlib import Path

# プロジェクト内のデータ置き場（parquetキャッシュ）
BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """backend/.env を読み込んで環境変数に反映（依存を増やさない軽量版）。
    既に設定済みの環境変数は上書きしない。"""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()
DATA_DIR = Path(os.getenv("KABUKA_DATA_DIR", BACKEND_DIR / "data"))

# バックテスト期間（無料データ=yfinanceで十分取れる範囲）
START_DATE = os.getenv("KABUKA_START", "2014-01-01")
END_DATE = os.getenv("KABUKA_END", "2024-12-31")

# 取引コスト（往復・bps）。批判役の指摘どおり必ず織り込む。
# ネット証券の手数料はほぼ無料化が進むが、スプレッド+スリッページ+マーケットインパクトを
# 保守的にまとめて見積もる。楽観/中立/悲観でスイープして感度を見る。
COST_BPS = float(os.getenv("KABUKA_COST_BPS", "20"))  # 片道20bps = 往復0.4%相当

# 分位数（クロスセクションを何分割するか）
N_QUANTILES = int(os.getenv("KABUKA_QUANTILES", "5"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
