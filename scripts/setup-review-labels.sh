#!/usr/bin/env bash
# scripts/setup-review-labels.sh
# コードレビュー運用に必要な GitHub ラベルを作成・設定するスクリプト

set -euo pipefail

DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      echo "使用法: $0 [--dry-run]"
      echo "  --dry-run: 実際の作成を行わず、実行予定のコマンドを表示します"
      exit 0
      ;;
    *)
      echo "エラー: 不明な引数です: $1" >&2
      echo "使用法: $0 [--dry-run] [-h|--help]" >&2
      exit 1
      ;;
  esac
done

# ラベル定義配列: "ラベル名|カラーコード|説明"
LABELS=(
  # Scope 関連
  "scope-review|0E8A16|初回 20 Scope コードレビューの Issue"
  "scope:unit|1D76DB|個別レビュー (R001〜R014)"
  "scope:integration|5319E7|結合レビュー (I001〜I005)"

  # Finding 関連
  "review-finding|B60205|コードレビューで検出された未解決指摘の追跡 Issue"

  # 重大度
  "severity:p0|D93F0B|P0 (致命的・即時修正)"
  "severity:p1|E99695|P1 (重大・Blocking)"
  "severity:p2|FBCA04|P2 (通常・Non-blocking)"
  "severity:p3|FEF2C0|P3 (軽微・改善)"

  # ステータス
  "status:in-review|EDEDED|レビュー中"
  "status:ready-to-fix|C2E0C6|修正待ち"
  "status:verified|0E8A16|修正確認完了"
)

echo "=== コードレビュー用 GitHub ラベルのセットアップ ==="
if [ "$DRY_RUN" = true ]; then
  echo "※ DRY-RUN モードで実行しています（実際の変更は行われません）"
fi

for item in "${LABELS[@]}"; do
  IFS="|" read -r name color desc <<< "$item"
  if [ "$DRY_RUN" = true ]; then
    echo "[DRY-RUN] gh label create \"$name\" --color \"$color\" --description \"$desc\" --force"
  else
    echo "Creating or updating label: $name ($color)"
    gh label create "$name" --color "$color" --description "$desc" --force
  fi
done

echo "=== セットアップ完了 ==="
