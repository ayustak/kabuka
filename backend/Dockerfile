# kabuka backend（API＋日次自動更新）をどこでも常時稼働させるためのイメージ
FROM python:3.12-slim

# LightGBM 用の OpenMP（モデル機能を使う場合）
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY . .

# スケジューラを有効化して常時稼働（平日18:30 JSTに日次パイプライン）
ENV KABUKA_ENABLE_SCHEDULER=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
