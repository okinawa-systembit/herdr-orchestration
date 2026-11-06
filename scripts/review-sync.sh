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
TMP_DIR="${REPO_ROOT}/.input/tmp"
mkdir -p "${DRAFT_DIR}" "${SENT_DIR}" "${TMP_DIR}"

create_temp_file() {
  local prefix="${1:-temp}"
  mktemp "${TMP_DIR}/${prefix}.XXXXXX"
}

format_markdown_file() {
  local target_file="${1}"
  if [[ ! -f "${target_file}" ]]; then
    return 0
  fi

  local prettier_bin=""
  if [[ -x "${REPO_ROOT}/node_modules/.bin/prettier" ]]; then
    prettier_bin="${REPO_ROOT}/node_modules/.bin/prettier"
  elif command -v prettier >/dev/null 2>&1; then
    prettier_bin="prettier"
  elif command -v npx >/dev/null 2>&1; then
    prettier_bin="npx --no-install prettier"
  fi

  if [[ -n "${prettier_bin}" ]]; then
    # P2-1: --ignore-path /dev/null で .prettierignore をバイパスし .input/ 配下を確実に整形
    # P2-2: env -u GH_TOKEN -u GITHUB_TOKEN かつ --no-config --no-editorconfig --prose-wrap preserve で外部JSの実行とトークン露出を遮断
    env -u GH_TOKEN -u GITHUB_TOKEN ${prettier_bin} \
      --write \
      --parser markdown \
      --no-config \
      --no-editorconfig \
      --prose-wrap preserve \
      --ignore-path /dev/null \
      "${target_file}" >/dev/null 2>&1 || true
  fi
}

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

  # origin/main に本スクリプトおよびライブラリが存在するか確認 (新規導入ブランチ作業中)
  if ! git -C "${REPO_ROOT}" cat-file -e origin/main:scripts/review-sync.sh >/dev/null 2>&1 || \
     ! git -C "${REPO_ROOT}" cat-file -e origin/main:scripts/lib/github_app_auth.py >/dev/null 2>&1 || \
     ! git -C "${REPO_ROOT}" cat-file -e origin/main:scripts/lib/pull_review.py >/dev/null 2>&1; then
    echo "警告: origin/main に scripts/review-sync.sh または scripts/lib/ がまだ存在しません (新規導入中)。" >&2
    echo "スクリプト開発・検証作業時は ALLOW_REVIEW_SYNC_DEV=1 を指定してください。" >&2
    exit 1
  fi

  # origin/main との差分を検査 (本体および scripts/lib/ 配下)
  if ! git -C "${REPO_ROOT}" diff --quiet origin/main -- scripts/review-sync.sh scripts/lib/ 2>/dev/null; then
    echo "エラー: scripts/review-sync.sh または scripts/lib/ が origin/main と一致しません。" >&2
    echo "git fetch と rebase を確認するか、スクリプト開発作業時は ALLOW_REVIEW_SYNC_DEV=1 を指定してください。" >&2
    exit 1
  fi

  # 未追跡 (untracked) ファイルの存在を検査 (scripts/lib/ 配下への不正モジュール混入防止)
  local untracked_files
  untracked_files="$(git -C "${REPO_ROOT}" ls-files --others --exclude-standard -- scripts/review-sync.sh scripts/lib/ 2>/dev/null || true)"
  if [[ -n "${untracked_files}" ]]; then
    echo "エラー: scripts/review-sync.sh または scripts/lib/ に未追跡のファイルが存在します:" >&2
    echo "${untracked_files}" >&2
    echo "不要なファイルを削除するか、スクリプト開発作業時は ALLOW_REVIEW_SYNC_DEV=1 を指定してください。" >&2
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
  bash scripts/review-sync.sh pull <reviewer|reviewee> [--pr <PR番号>]
  bash scripts/review-sync.sh comment <reviewer|reviewee> [--pr <PR番号>] [オプション...]
  bash scripts/review-sync.sh check <reviewer|reviewee> [オプション...]
  bash scripts/review-sync.sh pull-issue <reviewer|reviewee> <issue-number>
  bash scripts/review-sync.sh comment-issue <reviewer|reviewee> <issue-number> [オプション...]
  bash scripts/review-sync.sh pr-create [gh pr create のオプション...]

説明:
  pull          : PR から最新レビューを取り込みドラフトに展開 (PR番号は自動解決または --pr で指定)
  comment       : ドラフトを PR レビューコメントとして送信 (PR番号は自動解決または --pr で指定)
  check         : ドラフトの構文・メタデータ・機密情報を静的検証 (オフライン実行)
  pull-issue    : Issue から指摘・回答を取り込みドラフトに展開
  comment-issue : Issue へドラフト内容をコメントとして送信
  pr-create     : GitHub App (Bot) 認証を利用して PR を作成 (自 PR 制限を回避)

オプション:
  --pr <N>      : 対象 PR 番号を明示指定 (省略時はカレントブランチから自動解決)
  --status <val>: reviewer の判定 Status (Approved または 'Changes requested') を指定 (reviewer 必須)
  --round <N>   : 送信時の Round 番号を明示的に指定 (comment / comment-issue のみ有効)
EOF
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

SUBCOMMAND="$1"
shift

ROLE=""
ISSUE_NUMBER=""
STATUS_ARG=""
ROUND_ARG=""
PR_ARG=""

parse_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --status)
        if [[ $# -lt 2 ]]; then
          echo "エラー: --status には値 (Approved または 'Changes requested') が必要です。" >&2
          exit 1
        fi
        STATUS_ARG="$2"
        shift 2
        ;;
      --round)
        if [[ "${SUBCOMMAND}" == "check" ]]; then
          echo "エラー: check コマンドでは --round オプションは使用できません。" >&2
          exit 1
        fi
        if [[ $# -lt 2 ]]; then
          echo "エラー: --round には整数値が必要です。" >&2
          exit 1
        fi
        ROUND_ARG="$2"
        shift 2
        ;;
      --pr)
        if [[ "${SUBCOMMAND}" == "check" || "${SUBCOMMAND}" =~ -issue$ ]]; then
          echo "エラー: ${SUBCOMMAND} コマンドでは --pr オプションは使用できません。" >&2
          exit 1
        fi
        if [[ $# -lt 2 ]]; then
          echo "エラー: --pr には PR 番号 (正の整数) が必要です。" >&2
          exit 1
        fi
        if [[ ! "$2" =~ ^[0-9]+$ ]]; then
          echo "エラー: --pr には半角数字を指定してください (指定値: $2)" >&2
          exit 1
        fi
        PR_ARG="$2"
        shift 2
        ;;
      *)
        echo "エラー: 未知のオプション '$1' です。" >&2
        usage
        ;;
    esac
  done
}

case "${SUBCOMMAND}" in
  pr-create)
    # PR 作成: 引数はそのまま gh pr create へ渡す
    ;;
  pull)
    if [[ $# -lt 1 ]]; then
      echo "エラー: サブコマンド 'pull' には引数 <role> が必要です。" >&2
      usage
    fi
    ROLE="$1"
    shift
    if [[ "${ROLE}" != "reviewer" && "${ROLE}" != "reviewee" ]]; then
      echo "エラー: ロールは 'reviewer' または 'reviewee' を指定してください (指定値: ${ROLE})" >&2
      exit 1
    fi
    parse_options "$@"
    ;;
  comment|check)
    if [[ $# -lt 1 ]]; then
      echo "エラー: サブコマンド '${SUBCOMMAND}' には引数 <role> が必要です。" >&2
      usage
    fi
    ROLE="$1"
    shift
    if [[ "${ROLE}" != "reviewer" && "${ROLE}" != "reviewee" ]]; then
      echo "エラー: ロールは 'reviewer' または 'reviewee' を指定してください (指定値: ${ROLE})" >&2
      exit 1
    fi
    parse_options "$@"
    ;;
  pull-issue)
    if [[ $# -ne 2 ]]; then
      echo "エラー: サブコマンド 'pull-issue' には <role> と <issue-number> の2つの引数が必要です。" >&2
      usage
    fi
    ROLE="$1"
    ISSUE_NUMBER="$2"
    if [[ "${ROLE}" != "reviewer" && "${ROLE}" != "reviewee" ]]; then
      echo "エラー: ロールは 'reviewer' または 'reviewee' を指定してください (指定値: ${ROLE})" >&2
      exit 1
    fi
    if [[ ! "${ISSUE_NUMBER}" =~ ^[0-9]+$ ]]; then
      echo "エラー: Issue 番号には半角数字を指定してください (指定値: ${ISSUE_NUMBER})" >&2
      exit 1
    fi
    ;;
  comment-issue)
    if [[ $# -lt 2 ]]; then
      echo "エラー: サブコマンド 'comment-issue' には <role> と <issue-number> が必要です。" >&2
      usage
    fi
    ROLE="$1"
    ISSUE_NUMBER="$2"
    shift 2
    if [[ "${ROLE}" != "reviewer" && "${ROLE}" != "reviewee" ]]; then
      echo "エラー: ロールは 'reviewer' または 'reviewee' を指定してください (指定値: ${ROLE})" >&2
      exit 1
    fi
    if [[ ! "${ISSUE_NUMBER}" =~ ^[0-9]+$ ]]; then
      echo "エラー: Issue 番号には半角数字を指定してください (指定値: ${ISSUE_NUMBER})" >&2
      exit 1
    fi
    parse_options "$@"
    ;;
  *)
    echo "エラー: 未知のサブコマンド '${SUBCOMMAND}' です。" >&2
    usage
    ;;
esac

# ロール別バリデーション (指針8・9)
if [[ "${ROLE}" == "reviewer" ]]; then
  if [[ "${SUBCOMMAND}" =~ ^(check|comment|comment-issue)$ && -z "${STATUS_ARG}" ]]; then
    echo "エラー: reviewer は --status Approved または --status 'Changes requested' の指定が必要です。" >&2
    exit 1
  fi
elif [[ "${ROLE}" == "reviewee" ]]; then
  if [[ -n "${STATUS_ARG}" ]]; then
    echo "エラー: reviewee に --status を指定することはできません。判定 Status は reviewer のみ有効です。" >&2
    exit 1
  fi
fi

if [[ -n "${ROLE}" ]]; then
  DRAFT_FILE="${DRAFT_DIR}/${ROLE}-draft.md"
fi
mkdir -p "${DRAFT_DIR}" "${SENT_DIR}" "${TMP_DIR}"

# ---------------------------------------------------------------------------
# 3. GitHub 認証情報の解決およびインライン実行
# ---------------------------------------------------------------------------
# P3-1: 呼び出し元で export されていた場合の属性引き継ぎを防ぐため明示的に unset / declare +x
unset _REVIEW_SYNC_AUTH_TOKEN 2>/dev/null || true
declare +x _REVIEW_SYNC_AUTH_TOKEN="" 2>/dev/null || _REVIEW_SYNC_AUTH_TOKEN=""
BOT_AUTH_ACTIVE=0

run_gh() {
  if [[ -n "${_REVIEW_SYNC_AUTH_TOKEN}" ]]; then
    GH_TOKEN="${_REVIEW_SYNC_AUTH_TOKEN}" gh "$@"
  else
    gh "$@"
  fi
}

load_github_auth() {
  local err_file
  err_file="$(create_temp_file "review-auth-err")"
  trap 'rm -f "${err_file}"' RETURN EXIT

  local python_bin="python3"
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    python_bin="${REPO_ROOT}/.venv/bin/python3"
  fi

  local auth_script="${REPO_ROOT}/scripts/lib/github_app_auth.py"
  local token=""
  local auth_rc=0
  if [[ -f "${auth_script}" ]]; then
    token="$("${python_bin}" -I "${auth_script}" "${REPO_ROOT}" 2>"${err_file}")" || auth_rc=$?
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
    _REVIEW_SYNC_AUTH_TOKEN="${token}"
    BOT_AUTH_ACTIVE=1
    return 0
  fi

  # token が空（＝Bot 設定なしの個人認証モード）
  if [[ -n "${err_msg}" ]]; then
    echo "${err_msg}" >&2
  fi

  if [[ -n "${GH_TOKEN:-}" || -n "${GITHUB_TOKEN:-}" ]]; then
    return 0
  fi

  if ! run_gh auth status >/dev/null 2>&1; then
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
    # reviewer の判定 Status の検証
    if [[ -z "${STATUS_ARG}" ]]; then
      echo "エラー: reviewer は --status Approved または --status 'Changes requested' の指定が必要です。" >&2
      exit 1
    fi
    if [[ "${STATUS_ARG}" != "Approved" && "${STATUS_ARG}" != "Changes requested" ]]; then
      echo "エラー: --status には 'Approved' または 'Changes requested' を指定してください (指定値: ${STATUS_ARG})" >&2
      exit 1
    fi
  elif [[ "${ROLE}" == "reviewee" ]]; then
    if [[ -n "${STATUS_ARG}" ]]; then
      echo "エラー: reviewee に --status を指定することはできません。判定 Status は reviewer のみ有効です。" >&2
      exit 1
    fi
    # reviewee はメタデータ部に判定 Status: が含まれてはならない（Finding 内の Status は許可）
    if echo "${metadata}" | grep -E -q "^[[:space:]]*-?[[:space:]]*Status:"; then
      echo "エラー: reviewee のドラフトのメタデータ部に判定 Status: 行を含めることはできません。Finding 内の Status のみ更新してください。" >&2
      exit 1
    fi
  fi

  echo "OK: check passed (${ROLE})"
}

# PR 番号の解決 (明示指定時はそのまま使用、省略時は自動解決)
resolve_pr_number() {
  if [[ -n "${PR_ARG}" ]]; then
    echo "${PR_ARG}"
    return 0
  fi

  local pr_json pr_err_tmp
  pr_err_tmp="$(create_temp_file "review-pr-err")"
  trap 'rm -f "${pr_err_tmp}"' RETURN EXIT

  if ! pr_json="$(run_gh pr view --json number,url 2>"${pr_err_tmp}")"; then
    echo "エラー: 現在のブランチに対応する PR が見つかりません。" >&2
    cat "${pr_err_tmp}" >&2
    echo "gh pr create --draft などで PR を作成してから実行してください。" >&2
    rm -f "${pr_err_tmp}"
    trap - RETURN EXIT
    exit 1
  fi
  rm -f "${pr_err_tmp}"
  trap - RETURN EXIT

  local python_bin="python3"
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    python_bin="${REPO_ROOT}/.venv/bin/python3"
  fi

  local pr_num
  pr_num="$("${python_bin}" -I -c "import sys, json; print(json.loads(sys.stdin.read()).get('number', ''))" <<< "${pr_json}" 2>/dev/null || true)"
  pr_num="$(echo "${pr_num}" | tr -d '\r\n')"
  if [[ -z "${pr_num}" || ! "${pr_num}" =~ ^[0-9]+$ ]]; then
    echo "エラー: PR 番号の取得に失敗しました。" >&2
    exit 1
  fi
  echo "${pr_num}"
}

# 投稿処理共通
submit_comment() {
  local is_issue="$1"
  local target_id="$2"
  local target_type="pr"
  if [[ "${is_issue}" == "1" ]]; then
    target_type="issue"
  fi

  # 送信前にドラフトの自動 Markdown 整形 (Prettier) を実行
  format_markdown_file "${DRAFT_FILE}"

  # 送信前に check を自動実行
  do_check "${DRAFT_FILE}" >/dev/null

  local python_bin="python3"
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    python_bin="${REPO_ROOT}/.venv/bin/python3"
  fi

  # 1. ラウンド番号の解決（引数優先 -> state.json（対象一致検証付き） -> なければエラー、本文読み戻し廃止）
  local round=""
  if [[ -n "${ROUND_ARG}" ]]; then
    if [[ ! "${ROUND_ARG}" =~ ^[0-9]+$ ]]; then
      echo "エラー: --round には正の整数を指定してください: ${ROUND_ARG}" >&2
      exit 1
    fi
    round="${ROUND_ARG}"
  fi

  local state_file="${DRAFT_DIR}/state.json"
  if [[ -z "${round}" && -f "${state_file}" ]]; then
    local py_out
    py_out="$("${python_bin}" -I -c '
import sys, json

state_path, role_name, tgt_type, tgt_id = sys.argv[1:5]
try:
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    role_data = data.get(role_name, {})
    if not isinstance(role_data, dict):
        sys.exit(2)
    s_type = str(role_data.get("target_type", ""))
    s_id = str(role_data.get("target_id", ""))
    if s_type != tgt_type or s_id != str(tgt_id):
        print(f"MISMATCH:{s_type}:{s_id}", file=sys.stderr)
        sys.exit(3)
    val = role_data.get("round", "")
    print(val)
except FileNotFoundError:
    sys.exit(2)
except Exception as err:
    print(f"警告: state.json 読み込み失敗: {err}", file=sys.stderr)
    sys.exit(1)
' "${state_file}" "${ROLE}" "${target_type}" "${target_id}" 2>&1 || true)"

    if [[ "${py_out}" == *"MISMATCH:"* ]]; then
      local mismatch_val="${py_out#*MISMATCH:}"
      mismatch_val="$(echo "${mismatch_val}" | head -n 1)"
      echo "エラー: state.json の対象 (${mismatch_val}) が現在の送信先 (${target_type}:${target_id}) と一致しません。pull を再実行するか、--round <N> を明示的に指定してください。" >&2
      exit 1
    fi

    local clean_round
    clean_round="$(echo "${py_out}" | tr -d '\r\n')"
    if [[ "${clean_round}" =~ ^[0-9]+$ ]]; then
      round="${clean_round}"
    elif [[ -n "${clean_round}" ]]; then
      echo "エラー: state.json の round 値が無効です (${clean_round})。--round <N> を指定するか pull を再実行してください。" >&2
      exit 1
    fi
  fi

  if [[ -z "${round}" ]]; then
    echo "エラー: Round 番号を解決できませんでした。pull を実行するか、--round <N> を明示的に指定してください。" >&2
    exit 1
  fi

  # 2. reviewer の場合の判定 Status 解決 (P1-1: 引数必須・本文除外)
  local status_header=""
  if [[ "${ROLE}" == "reviewer" ]]; then
    if [[ -z "${STATUS_ARG}" ]]; then
      echo "エラー: reviewer は --status Approved または --status 'Changes requested' の指定が必要です。" >&2
      exit 1
    fi
    status_header="- Status: ${STATUS_ARG}"$'\n\n'
  fi

  # 送信用一時ファイルの作成（マーカーおよび外側制御値ヘッダー付与）
  local temp_body
  temp_body="$(create_temp_file "review-body")"
  trap 'rm -f "${temp_body}"' EXIT

  cat <<EOF > "${temp_body}"
<!-- review-sync role=${ROLE} round=${round} -->
EOF
  if [[ -n "${status_header}" ]]; then
    printf '%s' "${status_header}" >> "${temp_body}"
  fi

  # 下書き本文のメタデータ部に万一残っている Status: 行を送信用ファイルから除去して連結 (P1-1)
  if [[ "${ROLE}" == "reviewer" ]]; then
    awk '
      BEGIN { in_header = 1 }
      /^## / { in_header = 0 }
      in_header && /^[[:space:]]*-?[[:space:]]*Status:/ { next }
      { print }
    ' "${DRAFT_FILE}" >> "${temp_body}"
  else
    cat "${DRAFT_FILE}" >> "${temp_body}"
  fi

  # 送信実行 (常に --body-file 経由)
  if [[ "${is_issue}" == "1" ]]; then
    run_gh issue comment "${target_id}" --body-file "${temp_body}"
  else
    run_gh pr review "${target_id}" --comment --body-file "${temp_body}"
  fi

  # 送信成功後の退避 (同一秒送信時の上書き防止)
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)-$$"
  local dest="${SENT_DIR}/${timestamp}-${ROLE}-draft.md"
  if [[ -e "${dest}" ]]; then
    local seq=1
    while [[ -e "${SENT_DIR}/${timestamp}-${ROLE}-draft.${seq}.md" ]]; do
      seq=$((seq + 1))
    done
    dest="${SENT_DIR}/${timestamp}-${ROLE}-draft.${seq}.md"
  fi
  mv "${DRAFT_FILE}" "${dest}"
  rm -f "${temp_body}"
  trap - EXIT

  # 送信成功時に state.json を更新（last_sent_round を記録、P3-2: sys.argv 経由で安全呼び出し）
  if [[ -f "${state_file}" ]]; then
    "${python_bin}" -I -c '
import sys, json

state_path, role_name, round_str = sys.argv[1:4]
try:
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if role_name in data and isinstance(data[role_name], dict):
        data[role_name]["last_sent_round"] = int(round_str)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
except Exception as err:
    print(f"警告: state.json 更新失敗: {err}", file=sys.stderr)
' "${state_file}" "${ROLE}" "${round}" || true
  fi

  echo "OK: 送信が完了し、ドラフトを ${dest} に退避しました。"
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

  local comments_tmp inlines_tmp gh_err_tmp
  comments_tmp="$(create_temp_file "review-comments")"
  inlines_tmp="$(create_temp_file "review-inlines")"
  gh_err_tmp="$(create_temp_file "review-gh-err")"
  trap 'rm -f "${comments_tmp}" "${inlines_tmp}" "${gh_err_tmp}"' RETURN EXIT

  if [[ "${is_issue}" == "1" ]]; then
    if ! run_gh issue view "${target_id}" --json comments > "${comments_tmp}" 2>"${gh_err_tmp}"; then
      echo "エラー: Issue #${target_id} の取得に失敗しました:" >&2
      cat "${gh_err_tmp}" >&2
      exit 1
    fi
    echo "[]" > "${inlines_tmp}"
  else
    if ! run_gh pr view "${target_id}" --json comments,reviews > "${comments_tmp}" 2>"${gh_err_tmp}"; then
      echo "エラー: PR #${target_id} の取得に失敗しました:" >&2
      cat "${gh_err_tmp}" >&2
      exit 1
    fi

    # インラインレビューコメントの取得
    local repo_slug
    if ! repo_slug="$(run_gh repo view --json nameWithOwner -q .nameWithOwner 2>"${gh_err_tmp}")"; then
      echo "エラー: リポジトリ情報の取得に失敗しました:" >&2
      cat "${gh_err_tmp}" >&2
      exit 1
    fi

    if ! run_gh api --paginate --slurp "repos/${repo_slug}/pulls/${target_id}/comments" > "${inlines_tmp}" 2>"${gh_err_tmp}"; then
      echo "エラー: PR #${target_id} のインラインコメント取得に失敗しました:" >&2
      cat "${gh_err_tmp}" >&2
      exit 1
    fi
  fi

  # Python スクリプトで JSON を安全にパースして時系列ソート・ドラフト生成
  local python_bin="python3"
  if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    python_bin="${REPO_ROOT}/.venv/bin/python3"
  fi

  local pull_script="${REPO_ROOT}/scripts/lib/pull_review.py"
  if ! "${python_bin}" -I "${pull_script}" \
      "${DRAFT_FILE}" \
      "${ROLE}" \
      "${opposite_role}" \
      "${is_issue}" \
      "${target_id}" \
      "${comments_tmp}" \
      "${inlines_tmp}"; then
    exit 1
  fi

  rm -f "${comments_tmp}" "${inlines_tmp}" "${gh_err_tmp}"
  trap - RETURN EXIT
}

create_pull_request() {
  if [[ "${BOT_AUTH_ACTIVE:-0}" -ne 1 ]]; then
    echo "警告: GitHub App 設定が存在しません。個人認証で PR を作成すると、自 PR 制限により自分自身での Approve ができなくなります。" >&2
  fi

  # --body / --body-file 引数の Markdown 自動整形を適用 (元ファイルは変更せず一時コピーを使用)
  local args=()
  local temp_files=()
  trap 'rm -f "${temp_files[@]}" 2>/dev/null || true' RETURN EXIT

  local i=1
  while [[ $i -le $# ]]; do
    local arg="${!i}"
    if [[ "${arg}" == "--body" ]] && [[ $((i + 1)) -le $# ]]; then
      i=$((i + 1))
      local body_val="${!i}"
      local body_tmp
      body_tmp="$(create_temp_file "pr-body")"
      temp_files+=("${body_tmp}")
      printf '%s\n' "${body_val}" > "${body_tmp}"
      format_markdown_file "${body_tmp}"
      args+=("--body-file" "${body_tmp}")
    elif [[ "${arg}" == "--body-file" ]] && [[ $((i + 1)) -le $# ]]; then
      i=$((i + 1))
      local body_file_val="${!i}"
      local copy_tmp
      copy_tmp="$(create_temp_file "pr-body-copy")"
      temp_files+=("${copy_tmp}")
      if [[ "${body_file_val}" == "-" ]]; then
        cat > "${copy_tmp}"
      else
        cp "${body_file_val}" "${copy_tmp}"
      fi
      format_markdown_file "${copy_tmp}"
      args+=("--body-file" "${copy_tmp}")
    else
      args+=("${arg}")
    fi
    i=$((i + 1))
  done
  run_gh pr create "${args[@]}"
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
  pr-create)
    create_pull_request "$@"
    ;;
esac
