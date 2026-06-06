# VPSで常時稼働させる手順（Xserver VPS 2GB 想定 / Ubuntu 24.04）

24時間API＋平日18:30 JSTの日次自動更新（データ更新→キャッシュ再生成→推奨実績記録）を動かす。

## 0. VPS契約
- Xserver VPS **2GBプラン** / OSテンプレート **Ubuntu 24.04** を選択。
- 契約後、管理画面で表示される **IPアドレス** と **rootパスワード（または鍵）** を控える。

## 1. SSH接続 & 基本準備
```bash
ssh root@<VPSのIP>

apt update && apt -y upgrade
apt -y install python3.12 python3.12-venv git
# LightGBM(任意・モデル機能)を使うなら: apt -y install libgomp1

# 専用ユーザー
adduser --disabled-password --gecos "" kabuka
```

## 2. リポジトリ配置 & 依存導入
```bash
git clone <あなたのGitHubリポジトリURL> /opt/kabuka
chown -R kabuka:kabuka /opt/kabuka
sudo -u kabuka bash -c '
  cd /opt/kabuka
  python3.12 -m venv .venv
  .venv/bin/pip install -e backend
'
```

## 3. APIキーを .env に設定（コミットしない）
```bash
sudo -u kabuka tee /opt/kabuka/backend/.env >/dev/null <<EOF
KABUKA_JQUANTS_API_KEY=あなたのJ-Quantsキー
KABUKA_EDINET_KEY=あなたのEDINETキー
EOF
chmod 600 /opt/kabuka/backend/.env
```

## 4. 初回データ収集（10〜20分）
```bash
sudo -u kabuka bash -c '
  cd /opt/kabuka/backend
  ../.venv/bin/python -m app.ingestion.jquants_collect   # TOPIX500 PIT + 株価 + 財務
  ../.venv/bin/python scripts/build_dashboard_data.py     # キャッシュ生成
'
```

## 5. 常時稼働サービス登録（systemd）
```bash
cp /opt/kabuka/backend/deploy/kabuka.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kabuka
systemctl status kabuka         # active (running) を確認
```
これで再起動後も自動起動し、落ちても自動復帰。平日18:30 JSTに日次パイプラインが走る。

## 6. アクセス（セキュリティ重要）
APIは誰でも叩けてしまうため、**自分のIPだけ許可**するのが簡単で安全：
```bash
ufw allow OpenSSH
ufw allow from <自宅などのグローバルIP> to any port 8000
ufw enable
```
→ ブラウザで `http://<VPSのIP>:8000/` がダッシュボード。

（より安全にするなら Tailscale 導入や Nginx + Basic認証 + HTTPS。必要なら手順を出します。）

## 更新（コード変更を反映）
```bash
sudo -u kabuka git -C /opt/kabuka pull
sudo -u kabuka /opt/kabuka/.venv/bin/pip install -e /opt/kabuka/backend
systemctl restart kabuka
```

## 動作確認
```bash
curl http://localhost:8000/api/health           # {"status":"ok"}
curl http://localhost:8000/api/tracking/accuracy?horizon=mid
journalctl -u kabuka -f                          # ログ追尾（日次ジョブの実行も見える）
```
