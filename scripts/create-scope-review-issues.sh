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
      if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "エラー: --scope オプションには Scope ID（例: R001, I001）の指定が必要です" >&2
        echo "使用法: $0 [--dry-run] [--scope <ScopeID>] [-h|--help]" >&2
        exit 1
      fi
      TARGET_SCOPE="$2"
      shift 2
      ;;
    -h|--help)
      echo "使用法: $0 [--dry-run] [--scope <ScopeID>]"
      echo "  --dry-run: 実際の Issue 作成を行わず、コマンド・スキップ予定を表示します"
      echo "  --scope <ScopeID>: 特定の Scope（例: R001, I001）のみを対象にします"
      exit 0
      ;;
    *)
      echo "エラー: 不明な引数です: $1" >&2
      echo "使用法: $0 [--dry-run] [--scope <ScopeID>] [-h|--help]" >&2
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

# Scope ID のバリデーション (P2-02)
if [ -n "$TARGET_SCOPE" ]; then
  is_valid_scope=false
  for s in "${SCOPES[@]}"; do
    sid="${s%%-*}"
    if [ "$sid" = "$TARGET_SCOPE" ]; then
      is_valid_scope=true
      break
    fi
  done
  if [ "$is_valid_scope" = false ]; then
    echo "エラー: 指定された Scope ID '$TARGET_SCOPE' は定義されていません。" >&2
    echo "有効な Scope ID: R001..R014 (R006A, R006B), I001..I005" >&2
    exit 1
  fi
fi

echo "=== 初回 20 Scope レビュー Issue 起票支援スクリプト ==="
if [ "$DRY_RUN" = true ]; then
  echo "※ DRY-RUN モードで実行しています（実際の Issue 作成は行われません）"
fi

# 既存 Issue の取得と確認 (P2-01: 重複起票防止, P2-05: 取得件数拡張, P2-06: モック誤用防止)
echo "既存の Scope Review Issue を確認中..."
existing_titles=""
if [ "$DRY_RUN" = true ] && [ -n "${TEST_MOCK_EXISTING_SCOPE_ISSUES:-}" ]; then
  # テスト専用モック入力（dry-run かつテスト指定時のみ有効）
  echo "※ テスト用モックデータを使用して既存 Issue を照合します"
  existing_titles="$TEST_MOCK_EXISTING_SCOPE_ISSUES"
else
  if ! existing_titles=$(gh issue list --label "scope-review" --state all --limit 1000 --json title --jq '.[].title' 2>&1); then
    if [ "$DRY_RUN" = true ]; then
      echo "警告: 既存 Issue の取得に失敗しました: $existing_titles" >&2
      echo "※ dry-run モードのため、既存確認なしで予定コマンドを出力します。" >&2
      existing_titles=""
    else
      echo "エラー: 既存 Issue の取得に失敗しました。重複作成防止のため処理を安全停止します。" >&2
      echo "詳細: $existing_titles" >&2
      exit 1
    fi
  fi
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
created_count=0
skipped_count=0

for scope_dir in "${SCOPES[@]}"; do
  # スコープID（例: R001, R006A, I001）を抽出
  scope_id="${scope_dir%%-*}"

  if [ -n "$TARGET_SCOPE" ] && [ "$TARGET_SCOPE" != "$scope_id" ]; then
    continue
  fi

  req_file="$REPO_ROOT/docs/reviews/$scope_dir/request.md"
  if [ ! -f "$req_file" ]; then
    echo "エラー: リクエストファイルが存在しません: $req_file" >&2
    exit 1
  fi

  # 既存 Issue の重複チェック (P2-01)
  # タイトルに "[Scope Review] $scope_id:" が含まれるか確認
  check_pattern="\[Scope Review\] ${scope_id}:"
  if echo "$existing_titles" | grep -E -q "$check_pattern"; then
    if [ "$DRY_RUN" = true ]; then
      echo "[DRY-RUN: SKIP] Scope $scope_id の Issue は既に存在するためスキップします"
    else
      echo "スキップ: Scope $scope_id の Issue は既に存在します"
    fi
    skipped_count=$((skipped_count + 1))
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
  created_count=$((created_count + 1))
done

echo "=== 処理完了 (対象: $((created_count + skipped_count)) 件 / 作成予定・完了: $created_count 件 / スキップ: $skipped_count 件) ==="
