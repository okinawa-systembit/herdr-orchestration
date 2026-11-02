#!/usr/bin/env bash
# scripts/review-sync.sh
# GitHub PR / Issue と安全に連携するレビュー同期ラッパー

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. 基本パス設定
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
DRAFT_DIR="${REPO_ROOT}/.input/review"
SENT_DIR="${DRAFT_DIR}/sent"

# ---------------------------------------------------------------------------
# 1. 改ざん検知ガードレール
# ---------------------------------------------------------------------------
check_tamper() {
  local allow_dev="${ALLOW_REVIEW_SYNC_DEV:-0}"
  if [[ "${allow_dev}" == "1" ]]; then
    return 0
  fi

  # origin/main の存在を確認
  if ! git -C "${REPO_ROOT}" rev-parse --verify origin/main >/dev/null 2>&1; then
    echo "エラー: origin/main が見つかりません。git fetch origin main を実行してください。" >&2
    echo "スクリプト開発作業時は ALLOW_REVIEW_SYNC_DEV=1 を指定してください。" >&2
    exit 1
  fi

  # origin/main に本スクリプトが存在しない場合 (新規導入ブランチ作業中)
  if ! git -C "${REPO_ROOT}" cat-file -e origin/main:scripts/review-sync.sh >/dev/null 2>&1; then
    echo "警告: origin/main に scripts/review-sync.sh がまだ存在しません (新規導入中)。" >&2
    echo "スクリプト開発・検証作業時は ALLOW_REVIEW_SYNC_DEV=1 を指定してください。" >&2
    exit 1
  fi

  # origin/main との差分を検査 (REPO_ROOT 基準)
  if ! git -C "${REPO_ROOT}" diff --quiet origin/main -- scripts/review-sync.sh 2>/dev/null; then
    echo "エラー: scripts/review-sync.sh が origin/main と一致しません。" >&2
    echo "git fetch と rebase を確認するか、スクリプト開発作業時は ALLOW_REVIEW_SYNC_DEV=1 を指定してください。" >&2
    exit 1
  fi
}

check_tamper

# ---------------------------------------------------------------------------
# 2. 引数検証とホワイトリスト判定
# ---------------------------------------------------------------------------
usage() {
  cat <<'EOF' >&2
使用方法:
  bash scripts/review-sync.sh pull <reviewer|reviewee>
  bash scripts/review-sync.sh comment <reviewer|reviewee>
  bash scripts/review-sync.sh check <reviewer|reviewee>
  bash scripts/review-sync.sh pull-issue <reviewer|reviewee> <issue-number>
  bash scripts/review-sync.sh comment-issue <reviewer|reviewee> <issue-number>

説明:
  pull          : PR から最新レビューを取り込み .input/review/<role>-draft.md に展開 (PR番号は自動解決)
  comment       : .input/review/<role>-draft.md の内容を PR レビューコメントとして送信 (PR番号は自動解決)
  check         : .input/review/<role>-draft.md の構文・メタデータ・機密情報を静的検証 (オフライン実行)
  pull-issue    : Issue から指摘・回答を取り込みドラフトに展開
  comment-issue : Issue へドラフト内容をコメントとして送信
EOF
  exit 1
}

if [[ $# -lt 2 ]]; then
  usage
fi

SUBCOMMAND="$1"
ROLE="$2"

# ロール検証
if [[ "${ROLE}" != "reviewer" && "${ROLE}" != "reviewee" ]]; then
  echo "エラー: ロールは 'reviewer' または 'reviewee' を指定してください (指定値: ${ROLE})" >&2
  exit 1
fi

# サブコマンド・引数個数・Issue 番号検証
case "${SUBCOMMAND}" in
  pull|comment|check)
    if [[ $# -ne 2 ]]; then
      echo "エラー: サブコマンド '${SUBCOMMAND}' は引数 <role> のみ受け付けます (PR番号は自動解決されます)。" >&2
      usage
    fi
    ;;
  pull-issue|comment-issue)
    if [[ $# -ne 3 ]]; then
      echo "エラー: サブコマンド '${SUBCOMMAND}' には <role> と <issue-number> の2つの引数が必要です。" >&2
      usage
    fi
    ISSUE_NUMBER="$3"
    if [[ ! "${ISSUE_NUMBER}" =~ ^[0-9]+$ ]]; then
      echo "エラー: Issue 番号には半角数字を指定してください (指定値: ${ISSUE_NUMBER})" >&2
      exit 1
    fi
    ;;
  *)
    echo "エラー: 未知のサブコマンド '${SUBCOMMAND}' です。" >&2
    usage
    ;;
esac

DRAFT_FILE="${DRAFT_DIR}/${ROLE}-draft.md"
mkdir -p "${DRAFT_DIR}" "${SENT_DIR}"

# ---------------------------------------------------------------------------
# 3. GitHub 認証情報の解決
# ---------------------------------------------------------------------------
load_github_auth() {
  local err_file
  err_file="$(mktemp /tmp/review-auth-err.XXXXXX)"
  trap 'rm -f "${err_file}"' RETURN EXIT

  local python_bin="python3"
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    python_bin="${REPO_ROOT}/.venv/bin/python3"
  fi

  local auth_script="${REPO_ROOT}/scripts/lib/github_app_auth.py"
  local token=""
  local auth_rc=0
  if [[ -f "${auth_script}" ]]; then
    token="$("${python_bin}" "${auth_script}" "${REPO_ROOT}" 2>"${err_file}")" || auth_rc=$?
  else
    auth_rc=1
    echo "ERROR: 認証スクリプトが見つかりません: ${auth_script}" >"${err_file}"
  fi

  if [[ ${auth_rc} -ne 0 ]]; then
    cat "${err_file}" >&2
    rm -f "${err_file}"
    trap - RETURN EXIT
    exit 1
  fi

  local err_msg
  err_msg="$(cat "${err_file}")"
  rm -f "${err_file}"
  trap - RETURN EXIT

  token="$(echo "${token}" | tr -d '\r\n')"

  if [[ -n "${token}" ]]; then
    export GH_TOKEN="${token}"
    return 0
  fi

  # token が空（＝Bot 設定なしの個人認証モード）
  if [[ -n "${err_msg}" ]]; then
    echo "${err_msg}" >&2
  fi

  if [[ -n "${GH_TOKEN:-}" || -n "${GITHUB_TOKEN:-}" ]]; then
    return 0
  fi

  if ! gh auth status >/dev/null 2>&1; then
    cat <<'EOF' >&2
エラー: GitHub の認証情報が見つかりません。
以下のいずれかの方法で認証を設定してください:
  1. 環境変数 (GITHUB_BOT_APP_ID, GITHUB_BOT_INSTALL_ID, GITHUB_APP_PRIVATE_KEY_PATH) を設定
  2. リポジトリルートに .env.github-app を作成して上記を設定 (.env.github-app.example 参照)
  3. gh auth login で GitHub CLI にログイン
EOF
    exit 1
  fi
}

# check 以外のサブコマンドで認証を読み込む
if [[ "${SUBCOMMAND}" != "check" ]]; then
  load_github_auth
fi

# ---------------------------------------------------------------------------
# 4. サブコマンド実装
# ---------------------------------------------------------------------------

# 静的検証 (check)
do_check() {
  local target_file="${1:-${DRAFT_FILE}}"
  if [[ ! -f "${target_file}" || ! -s "${target_file}" ]]; then
    echo "エラー: ドラフトファイルが存在しないか空です: ${target_file}" >&2
    exit 1
  fi

  # 機密情報パターン検査 (ghs_ / gho_ / PAT / PEM秘密鍵ヘッダー)
  if grep -E -q "gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{22,}|-----BEGIN [A-Z ]*PRIVATE KEY-----" "${target_file}"; then
    echo "エラー: ドラフトファイル内に機密情報（GitHub トークンまたは秘密鍵）が検出されました。" >&2
    exit 1
  fi

  # メタデータ部の抽出（最初の見出し '## ' より前のヘッダー部分）
  local metadata
  metadata="$(awk '/^## /{exit} {print}' "${target_file}")"

  if [[ "${ROLE}" == "reviewer" ]]; then
    # reviewer はメタデータ部に Status: (Approved|Changes requested) が必須
    if ! echo "${metadata}" | grep -E -q "^[[:space:]]*-?[[:space:]]*Status:[[:space:]]*(Approved|Changes requested)"; then
      echo "エラー: reviewer のドラフトメタデータ部に '- Status: Approved' または '- Status: Changes requested' が必要です。" >&2
      exit 1
    fi
  elif [[ "${ROLE}" == "reviewee" ]]; then
    # reviewee はメタデータ部に判定 Status: が含まれてはならない（Finding 内の Status は許可）
    if echo "${metadata}" | grep -E -q "^[[:space:]]*-?[[:space:]]*Status:"; then
      echo "エラー: reviewee のドラフトのメタデータ部に判定 Status: 行を含めることはできません。Finding 内の Status のみ更新してください。" >&2
      exit 1
    fi
  fi

  echo "OK: check passed (${ROLE})"
}

# PR 番号の自動解決
resolve_pr_number() {
  local pr_json
  if ! pr_json="$(gh pr view --json number,url 2>&1)"; then
    echo "エラー: 現在のブランチに対応する PR が見つかりません。" >&2
    echo "${pr_json}" >&2
    echo "gh pr create --draft などで PR を作成してから実行してください。" >&2
    exit 1
  fi
  local pr_num
  pr_num="$(python3 -c "import sys, json; print(json.loads(sys.stdin.read()).get('number', ''))" <<< "${pr_json}" 2>/dev/null || true)"
  if [[ -z "${pr_num}" ]]; then
    echo "エラー: PR 番号の取得に失敗しました。" >&2
    exit 1
  fi
  echo "${pr_num}"
}

# 投稿処理共通
submit_comment() {
  local is_issue="$1"
  local target_id="$2"

  # 送信前に check を自動実行
  do_check "${DRAFT_FILE}" >/dev/null

  # ラウンド番号の抽出（ドラフト内に指定があれば利用、なければ 1）
  local round=1
  local round_match
  round_match="$(grep -E "^[[:space:]]*-?[[:space:]]*Round:[[:space:]]*[0-9]+" "${DRAFT_FILE}" | head -n 1 || true)"
  if [[ -n "${round_match}" ]]; then
    round="$(echo "${round_match}" | grep -o '[0-9]\+')"
  fi

  # 送信用一時ファイルの作成（マーカー付与）
  local temp_body
  temp_body="$(mktemp /tmp/review-body.XXXXXX)"
  trap 'rm -f "${temp_body}"' EXIT

  cat <<EOF > "${temp_body}"
<!-- review-sync role=${ROLE} round=${round} -->
EOF
  cat "${DRAFT_FILE}" >> "${temp_body}"

  # 送信実行 (常に --body-file 経由)
  if [[ "${is_issue}" == "1" ]]; then
    gh issue comment "${target_id}" --body-file "${temp_body}"
  else
    gh pr review --comment --body-file "${temp_body}"
  fi

  # 送信成功後の退避
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  mv "${DRAFT_FILE}" "${SENT_DIR}/${timestamp}-${ROLE}-draft.md"
  rm -f "${temp_body}"
  trap - EXIT

  echo "OK: 送信が完了し、ドラフトを ${SENT_DIR}/${timestamp}-${ROLE}-draft.md に退避しました。"
}

# 取り込み処理共通
pull_content() {
  local is_issue="$1"
  local target_id="$2"

  # 未送信ドラフトの保護
  if [[ -f "${DRAFT_FILE}" && -s "${DRAFT_FILE}" ]]; then
    echo "エラー: 未送信のドラフトファイルが存在します (${DRAFT_FILE})。" >&2
    echo "送信するか、別名へ退避してから pull を実行してください。" >&2
    exit 1
  fi

  local opposite_role
  if [[ "${ROLE}" == "reviewer" ]]; then
    opposite_role="reviewee"
  else
    opposite_role="reviewer"
  fi

  local comments_tmp inlines_tmp
  comments_tmp="$(mktemp /tmp/review-comments.XXXXXX)"
  inlines_tmp="$(mktemp /tmp/review-inlines.XXXXXX)"
  trap 'rm -f "${comments_tmp}" "${inlines_tmp}"' RETURN EXIT

  if [[ "${is_issue}" == "1" ]]; then
    if ! gh issue view "${target_id}" --json comments > "${comments_tmp}" 2>&1; then
      echo "エラー: Issue #${target_id} の取得に失敗しました:" >&2
      cat "${comments_tmp}" >&2
      exit 1
    fi
    echo "[]" > "${inlines_tmp}"
  else
    if ! gh pr view "${target_id}" --json comments,reviews > "${comments_tmp}" 2>&1; then
      echo "エラー: PR #${target_id} の取得に失敗しました:" >&2
      cat "${comments_tmp}" >&2
      exit 1
    fi

    # インラインレビューコメントの取得
    local repo_slug
    if ! repo_slug="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>&1)"; then
      echo "エラー: リポジトリ情報の取得に失敗しました:" >&2
      echo "${repo_slug}" >&2
      exit 1
    fi

    if ! gh api --paginate --slurp "repos/${repo_slug}/pulls/${target_id}/comments" > "${inlines_tmp}" 2>&1; then
      echo "エラー: PR #${target_id} のインラインコメント取得に失敗しました:" >&2
      cat "${inlines_tmp}" >&2
      exit 1
    fi
  fi

  # Python スクリプトで JSON を安全にパースして時系列ソート・ドラフト生成
  local python_bin="python3"
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    python_bin="${REPO_ROOT}/.venv/bin/python3"
  fi

  local pull_script="${REPO_ROOT}/scripts/lib/pull_review.py"
  if ! "${python_bin}" "${pull_script}" \
      "${DRAFT_FILE}" \
      "${ROLE}" \
      "${opposite_role}" \
      "${is_issue}" \
      "${target_id}" \
      "${comments_tmp}" \
      "${inlines_tmp}"; then
    exit 1
  fi

  rm -f "${comments_tmp}" "${inlines_tmp}"
  trap - RETURN EXIT
}

# ---------------------------------------------------------------------------
# 5. ディスパッチ
# ---------------------------------------------------------------------------
case "${SUBCOMMAND}" in
  check)
    do_check
    ;;
  comment)
    PR_NUM="$(resolve_pr_number)"
    submit_comment 0 "${PR_NUM}"
    ;;
  comment-issue)
    submit_comment 1 "${ISSUE_NUMBER}"
    ;;
  pull)
    PR_NUM="$(resolve_pr_number)"
    pull_content 0 "${PR_NUM}"
    ;;
  pull-issue)
    pull_content 1 "${ISSUE_NUMBER}"
    ;;
esac
