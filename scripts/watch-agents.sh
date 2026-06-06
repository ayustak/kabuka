#!/usr/bin/env bash
# kabuka調査チームの各エージェントの作業状況をtmuxで並べて見る。
# 使い方: watch-agents.sh [workflow-run-dir]
#   引数省略時は最新のworkflow runを自動検出。
set -u

BASE="/Users/sk/.claude/projects/-Users-sk-Dev-kabuka"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VIEW="$SCRIPT_DIR/agent-view.sh"

# run ディレクトリ決定（最新のwf_*を探す）
if [ $# -ge 1 ]; then
  RUN_DIR="$1"
else
  RUN_DIR=$(find "$BASE" -type d -name 'wf_*' -path '*subagents/workflows*' \
            -exec stat -f '%m %N' {} \; 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
fi

if [ -z "${RUN_DIR:-}" ] || [ ! -d "$RUN_DIR" ]; then
  echo "workflow runディレクトリが見つかりません" >&2; exit 1
fi
echo "監視対象: $RUN_DIR"

SESSION="kabuka-agents"
tmux kill-session -t "$SESSION" 2>/dev/null

# agent jsonl を列挙（bash3.2にmapfileが無いので手動）
FILES=()
while IFS= read -r line; do
  [ -n "$line" ] && FILES+=("$line")
done < <(ls -t "$RUN_DIR"/agent-*.jsonl 2>/dev/null)
if [ ${#FILES[@]} -eq 0 ]; then
  echo "agent jsonlがまだありません（起動直後かも）" >&2; exit 1
fi

# 最初のペインを作成
tmux new-session -d -s "$SESSION" -x "$(tput cols)" -y "$(tput lines)" \
  "bash '$VIEW' '${FILES[0]}'"

# 残りを分割（タイル配置）
for ((i=1; i<${#FILES[@]}; i++)); do
  tmux split-window -t "$SESSION" "bash '$VIEW' '${FILES[$i]}'"
  tmux select-layout -t "$SESSION" tiled
done
tmux select-layout -t "$SESSION" tiled
tmux set -t "$SESSION" mouse on

echo "起動完了。アタッチ:  tmux attach -t $SESSION"
echo "（デタッチ: Ctrl-b d / 終了: tmux kill-session -t ${SESSION}）"
