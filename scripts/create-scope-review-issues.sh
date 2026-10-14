#!/usr/bin/env bash
# scripts/create-scope-review-issues.sh
# 初回 20 Scope のコードレビュー Issue を起票する支援スクリプト

set -euo pipefail

DRY_RUN=false
TARGET_SCOPE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --scope)
      TARGET_SCOPE="$2"
      shift 2
      ;;
    -h|--help)
      echo "使用法: $0 [--dry-run] [--scope <ScopeID>]"
      echo "  --dry-run: 実際の Issue 作成を行わず、コマンドを表示します"
      echo "  --scope <ScopeID>: 特定の Scope（例: R001, I001）のみを対象にします"
      exit 0
      ;;
    *)
      echo "不明な引数: $1" >&2
      exit 1
      ;;
  esac
done

# 対象 Scope のディレクトリ一覧（順序順）
SCOPES=(
  "R001-primitives"
  "R002-identity-cli"
  "R003-live-agent"
  "R004-registry-schema"
  "R005-registry-io"
  "R006A-registry-lifecycle"
  "R006B-registry-ops"
  "R007-dispatch"
  "R008-handoff"
  "R009-result"
  "R010-completion"
  "R011-public-cli"
  "R012-installer"
  "R013-skills"
  "R014-cicd"
  "I001-registry-handoff"
  "I002-handoff-result"
  "I003-result-completion"
  "I004-cli-contract"
  "I005-ecosystem"
)

echo "=== 初回 20 Scope レビュー Issue 起票支援スクリプト ==="
if [ "$DRY_RUN" = true ]; then
  echo "※ DRY-RUN モードで実行しています（実際の Issue 作成は行われません）"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for scope_dir in "${SCOPES[@]}"; do
  # スコープID（例: R001, R006A, I001）を抽出
  scope_id="${scope_dir%%-*}"

  if [ -n "$TARGET_SCOPE" ] && [ "$TARGET_SCOPE" != "$scope_id" ]; then
    continue
  fi

  req_file="$REPO_ROOT/docs/reviews/$scope_dir/request.md"
  if [ ! -f "$req_file" ]; then
    echo "警告: $req_file が見つかりません。スキップします。" >&2
    continue
  fi

  # request.md の 1 行目からタイトル概要を取得（"# レビュー依頼: " を除去）
  first_line=$(head -n 1 "$req_file")
  title_text=$(echo "$first_line" | sed 's/^# レビュー依頼: //')
  issue_title="[Scope Review] $title_text"

  # ラベルの設定
  labels="scope-review"
  if [[ "$scope_id" =~ ^R ]]; then
    labels="$labels,scope:unit"
  elif [[ "$scope_id" =~ ^I ]]; then
    labels="$labels,scope:integration"
  fi

  if [ "$DRY_RUN" = true ]; then
    echo "[DRY-RUN] gh issue create --title \"$issue_title\" --body-file \"$req_file\" --label \"$labels\""
  else
    echo "Creating issue: $issue_title"
    gh issue create --title "$issue_title" --body-file "$req_file" --label "$labels"
  fi
done

echo "=== 処理完了 ==="
