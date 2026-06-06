#!/usr/bin/env bash
# 1エージェントのtranscript(jsonl)を人間可読に整形して追従表示する。
# 使い方: agent-view.sh <agent-jsonl>
set -u
f="$1"

# 最初のユーザープロンプトから担当トピックを推定してヘッダに出す
detect_topic() {
  local txt
  txt=$(jq -rs 'map(select(.type=="user")) | .[0].message.content
        | if type=="array" then (map(.text? // "") | join(" ")) else tostring end' "$f" 2>/dev/null)
  case "$txt" in
    *J-Quants*仕様*|*料金プラン*) echo "🟦 J-Quants調査" ;;
    *特徴量*モデル*|*LightGBM*ベストプラクティス*|*クロスセクション*) echo "🟩 予測モデル調査" ;;
    *バックテスト*落とし穴*|*リーク*過学習*) echo "🟥 バックテスト調査" ;;
    *批判役*) echo "🟨 批判役" ;;
    *) echo "エージェント $(basename "$f")" ;;
  esac
}

# jsonl 1行を1〜数行の要約に整形
fmt() {
  jq -r '
    . as $e
    | ($e.message.content // []) as $c
    | if ($c|type) != "array" then empty else
      ($c[] |
        if .type=="text" and (.text|length>0) then
          "[36m💬 [0m" + (.text | gsub("\n";" ") | .[0:400])
        elif .type=="tool_use" then
          "[33m🔧 " + .name + "[0m " +
            (try (.input | (.query // .url // .pattern // (.command|.[0:80]) // "") ) catch "" | tostring | .[0:120])
        elif .type=="tool_result" then
          "[90m   ↳ result[0m"
        else empty end)
      end
  ' 2>/dev/null
}

clear
echo -e "\033[1m$(detect_topic)\033[0m"
echo "─────────────────────────────────────────"
# 既存分を表示してから追従
fmt < <(cat "$f")
tail -n0 -F "$f" 2>/dev/null | while IFS= read -r line; do
  printf '%s\n' "$line" | fmt
done
