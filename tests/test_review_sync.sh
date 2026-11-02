#!/usr/bin/env bash
# tests/test_review_sync.sh
# scripts/review-sync.sh のユニット・結合テストスイート

set -euo pipefail

# 外部環境変数の影響を排除
unset ALLOW_REVIEW_SYNC_DEV || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_SCRIPT="${SCRIPT_DIR}/scripts/review-sync.sh"

FAILED=0
PASSED=0

pass() {
  echo "PASS: $1"
  PASSED=$((PASSED + 1))
}

fail() {
  echo "FAIL: $1 - $2" >&2
  FAILED=$((FAILED + 1))
}

# 一時テスト環境のセットアップ
TEST_TMP="$(mktemp -d /tmp/test-review-sync.XXXXXX)"
trap 'rm -rf "${TEST_TMP}"' EXIT

setup_mock_env() {
  local env_dir="${TEST_TMP}/$1"
  mkdir -p "${env_dir}/bin"
  mkdir -p "${env_dir}/worktree/.input/review"
  mkdir -p "${env_dir}/worktree/scripts/lib"
  cp "${TARGET_SCRIPT}" "${env_dir}/worktree/scripts/review-sync.sh" 2>/dev/null || true
  cp -r "${SCRIPT_DIR}/scripts/lib/"* "${env_dir}/worktree/scripts/lib/" 2>/dev/null || true

  # git リポジトリとして初期化して実作業ツリーから完全に隔離
  git -C "${env_dir}/worktree" init -q -b main
  git -C "${env_dir}/worktree" config user.email "test@example.com"
  git -C "${env_dir}/worktree" config user.name "Test Runner"
  git -C "${env_dir}/worktree" add scripts/
  git -C "${env_dir}/worktree" commit -q -m "initial commit"

  # mock gh コマンド
  cat <<'EOF' > "${env_dir}/bin/gh"
#!/usr/bin/env bash
# Mock gh CLI
echo "mock-gh $*" >> "${MOCK_GH_LOG:-/dev/null}"
if [[ "$*" == *"pr view --json number,url"* ]]; then
  if [[ "${MOCK_PR_NOT_FOUND:-0}" == "1" ]]; then
    exit 1
  fi
  echo '{"number": 2, "url": "https://github.com/example/repo/pull/2"}'
  exit 0
elif [[ "$*" == *"pr review --comment --body-file"* ]]; then
  for arg in "$@"; do
    if [[ -f "$arg" ]]; then
      cat "$arg" > "${MOCK_LAST_BODY:-/dev/null}"
    fi
  done
  exit "${MOCK_GH_EXIT:-0}"
elif [[ "$*" == *"pr create"* ]]; then
  echo "mock-pr-create: GH_TOKEN=${GH_TOKEN:-none} args=$*" >> "${MOCK_GH_LOG:-/dev/null}"
  echo "https://github.com/example/repo/pull/123"
  exit 0
elif [[ "$*" == *"issue comment"* ]]; then
  for arg in "$@"; do
    if [[ -f "$arg" ]]; then
      cat "$arg" > "${MOCK_LAST_BODY:-/dev/null}"
    fi
  done
  exit "${MOCK_GH_EXIT:-0}"
elif [[ "$*" == *"pr view"* ]]; then
  if [[ "${MOCK_PR_VIEW_FAIL:-0}" == "1" ]]; then
    echo "mock-gh: PR view failed (network error)" >&2
    exit 1
  fi
  cat <<'JSON'
{
  "comments": [],
  "reviews": []
}
JSON
  exit 0
elif [[ "$*" == *"repo view"* ]]; then
  if [[ "${MOCK_REPO_VIEW_FAIL:-0}" == "1" ]]; then
    echo "mock-gh: repo view failed" >&2
    exit 1
  fi
  echo "example/repo"
  exit 0
elif [[ "$*" == *"pulls/"*"/comments"* ]]; then
  if [[ "${MOCK_INLINE_COMMENTS_FAIL:-0}" == "1" ]]; then
    echo "mock-gh: inline comments API failed" >&2
    exit 1
  fi
  echo "[]"
  exit 0
fi
exit 0
EOF
  chmod +x "${env_dir}/bin/gh"

  echo "${env_dir}"
}

echo "=== Running tests for scripts/review-sync.sh ==="

# ---------------------------------------------------------------------------
# Test 1: スクリプトの存在確認
# ---------------------------------------------------------------------------
if [[ ! -f "${TARGET_SCRIPT}" ]]; then
  fail "Target script exists" "${TARGET_SCRIPT} does not exist"
else
  pass "Target script exists"
fi

# ---------------------------------------------------------------------------
# Test 2: 引数検証 - 未知のサブコマンド
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t2")
cd "${TEST_ENV}/worktree"
if PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh unknown_subcmd reviewer 2>/dev/null; then
  fail "Reject unknown subcommand" "Expected non-zero exit code"
else
  pass "Reject unknown subcommand"
fi

# ---------------------------------------------------------------------------
# Test 3: 引数検証 - 余分な引数
# ---------------------------------------------------------------------------
cd "${TEST_ENV}/worktree"
if PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer extra_arg 2>/dev/null; then
  fail "Reject extra arguments" "Expected non-zero exit code"
else
  pass "Reject extra arguments"
fi

# ---------------------------------------------------------------------------
# Test 4: 引数検証 - 無効な role
# ---------------------------------------------------------------------------
cd "${TEST_ENV}/worktree"
if PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check invalid_role 2>/dev/null; then
  fail "Reject invalid role" "Expected non-zero exit code"
else
  pass "Reject invalid role"
fi

# ---------------------------------------------------------------------------
# Test 5: 引数検証 - 監査 Issue の無効な Issue 番号
# ---------------------------------------------------------------------------
cd "${TEST_ENV}/worktree"
if PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull-issue reviewer not_a_number 2>/dev/null; then
  fail "Reject non-numeric issue number" "Expected non-zero exit code"
else
  pass "Reject non-numeric issue number"
fi

# ---------------------------------------------------------------------------
# Test 6: 改ざん検知 - origin/main 不在および差分検知 (本体および scripts/lib/)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t6")
cd "${TEST_ENV}/worktree"

# 6-1: origin/main が存在しない場合は停止すること
ERR_MSG_NO_ORIGIN=$(bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_MSG_NO_ORIGIN}" == *"origin/main が見つかりません"* ]]; then
  pass "Tamper detection stops when origin/main is missing"
else
  fail "Tamper detection stops when origin/main is missing" "Output: ${ERR_MSG_NO_ORIGIN}"
fi

# 6-2: origin/main を作成し、本体に差分がある場合は検知して停止すること
git update-ref refs/remotes/origin/main HEAD
echo "# tamper change" >> scripts/review-sync.sh

ERR_MSG_TAMPER=$(bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_MSG_TAMPER}" == *"scripts/review-sync.sh または scripts/lib/ が origin/main と一致しません"* ]]; then
  pass "Tamper detection stops when diff exists without ALLOW_REVIEW_SYNC_DEV"
else
  fail "Tamper detection stops when diff exists without ALLOW_REVIEW_SYNC_DEV" "Output: ${ERR_MSG_TAMPER}"
fi

# 6-3: 本体を元に戻し、scripts/lib/ 配下に差分がある場合も検知して停止すること (RV064 P2-1)
git checkout -- scripts/review-sync.sh
echo "# tamper lib" >> scripts/lib/github_app_auth.py

ERR_MSG_LIB_TAMPER=$(bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_MSG_LIB_TAMPER}" == *"scripts/review-sync.sh または scripts/lib/ が origin/main と一致しません"* ]]; then
  pass "Tamper detection stops when diff in scripts/lib exists (RV064 P2-1)"
else
  fail "Tamper detection stops when diff in scripts/lib exists (RV064 P2-1)" "Output: ${ERR_MSG_LIB_TAMPER}"
fi
git checkout -- scripts/lib/github_app_auth.py

# 6-4: scripts/lib/ 配下に未追跡ファイルが存在する場合も検知して停止すること (RV065 P3-1)
echo "print('shadow')" > scripts/lib/untracked_module.py

ERR_MSG_UNTRACKED=$(bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_MSG_UNTRACKED}" == *"未追跡のファイルが存在します"* ]]; then
  pass "Tamper detection stops when untracked files exist in scripts/lib (RV065 P3-1)"
else
  fail "Tamper detection stops when untracked files exist in scripts/lib (RV065 P3-1)" "Output: ${ERR_MSG_UNTRACKED}"
fi
rm -f scripts/lib/untracked_module.py

# 6-5: ALLOW_REVIEW_SYNC_DEV=1 でバイパスできること
ERR_MSG_BYPASS=$(ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_MSG_BYPASS}" != *"origin/main と一致しません"* && "${ERR_MSG_BYPASS}" == *"ドラフトファイルが存在しない"* ]]; then
  pass "Tamper detection bypassed with ALLOW_REVIEW_SYNC_DEV=1"
else
  fail "Tamper detection bypassed with ALLOW_REVIEW_SYNC_DEV=1" "Output: ${ERR_MSG_BYPASS}"
fi

# ---------------------------------------------------------------------------
# Test 7: PR 自動解決 - PR が見つからない場合に停止
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t7")
MOCK_LOG="${TEST_ENV}/gh.log"
cd "${TEST_ENV}/worktree"
ERR_OUTPUT=$(MOCK_GH_LOG="${MOCK_LOG}" MOCK_PR_NOT_FOUND=1 PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"PR"* || "${ERR_OUTPUT}" == *"pull request"* ]]; then
  pass "Stop when PR not found"
else
  fail "Stop when PR not found" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 7b: PR 自動解決 - カレントディレクトリに追跡外の json.py があってもシャドーイングされず安全に解決できること (RV068 P2-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t7b")
MOCK_LOG="${TEST_ENV}/gh.log"
cd "${TEST_ENV}/worktree"

# カレントディレクトリに悪意のある json.py を配置
cat <<'EOF' > json.py
raise RuntimeError("MALICIOUS_JSON_SHADOW_RAN")
EOF

DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 1
- Status: Changes requested
## Summary
test summary
EOF

# comment reviewer を実行（内部で resolve_pr_number が呼ばれる）
PR_OUTPUT=$(MOCK_GH_LOG="${MOCK_LOG}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer 2>&1 || true)
rm -f json.py

if [[ "${PR_OUTPUT}" == *"OK: 送信が完了し"* ]] && [[ "${PR_OUTPUT}" != *"MALICIOUS_JSON_SHADOW_RAN"* ]]; then
  pass "resolve_pr_number uses python3 -I and ignores malicious json.py shadow in cwd (RV068 P2-1)"
else
  fail "resolve_pr_number uses python3 -I and ignores malicious json.py shadow in cwd (RV068 P2-1)" "Output: ${PR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 8: 未送信ドラフトの保護 - pull 時に既存未送信ドラフトがあれば停止
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t8")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
echo "unsubmitted draft content" > "${DRAFT_PATH}"

cd "${TEST_ENV}/worktree"
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewer 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"未送信のドラフトファイルが存在します"* ]]; then
  pass "Protect existing unsubmitted draft on pull"
else
  fail "Protect existing unsubmitted draft on pull" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 9: check - 機密情報の検出 (ghs_ / gho_ / PAT / 秘密鍵ヘッダー)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t9")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Status: Changes requested
Token: ghs_12345678901234567890123456789012
EOF

cd "${TEST_ENV}/worktree"
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"機密情報"* ]]; then
  pass "Detect leaked ghs_ token in check"
else
  fail "Detect leaked ghs_ token in check" "Output: ${ERR_OUTPUT}"
fi

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Status: Changes requested
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
EOF
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"機密情報"* ]]; then
  pass "Detect leaked private key in check"
else
  fail "Detect leaked private key in check" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 10: check - reviewer メタデータに Status がない場合停止
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t10")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 1
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"reviewer のドラフトメタデータ部に"* ]]; then
  pass "Reject reviewer draft missing metadata Status"
else
  fail "Reject reviewer draft missing metadata Status" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 11: check - reviewee メタデータに Status がある場合停止
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t11")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
- Status: Approved
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"reviewee のドラフトのメタデータ部に判定 Status: 行を含めることはできません"* ]]; then
  pass "Reject reviewee draft with metadata Status"
else
  fail "Reject reviewee draft with metadata Status" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 12: check - reviewee の Finding 内 Status は許可
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Summary
summary text
## Findings
### F001 Title
- Status: accepted
- Decision: accept
EOF

cd "${TEST_ENV}/worktree"
OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee)
if [[ "${OUT}" == *"OK: check passed (reviewee)"* ]]; then
  pass "Pass valid reviewee draft with only finding-level Status"
else
  fail "Pass valid reviewee draft with only finding-level Status" "Output: ${OUT}"
fi

# ---------------------------------------------------------------------------
# Test 13: comment - マーカー付与と sent/ への退避
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t13")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 2
- Status: Changes requested
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer

if grep -q "<!-- review-sync role=reviewer round=2 -->" "${LAST_BODY}"; then
  pass "Inject marker into submitted comment"
else
  fail "Inject marker into submitted comment" "Marker not found in body"
fi

if [[ ! -f "${DRAFT_PATH}" ]] && compgen -G "${TEST_ENV}/worktree/.input/review/sent/*-reviewer-draft.md" >/dev/null; then
  pass "Archive draft to sent/ on comment success"
else
  fail "Archive draft to sent/ on comment success" "Draft was not moved to sent/"
fi

# ---------------------------------------------------------------------------
# Test 13b: comment - Round 行に複数数字がある場合に最初の数字を採用 (RV064 P3-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t13b")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 2 (prev 1)
- Status: Changes requested
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer

if grep -q "^<!-- review-sync role=reviewer round=2 -->$" "${LAST_BODY}"; then
  pass "Extract first numeric round from metadata when multiple numbers exist (RV064 P3-1)"
else
  fail "Extract first numeric round from metadata when multiple numbers exist (RV064 P3-1)" "Marker: $(head -n 2 "${LAST_BODY}")"
fi

# ---------------------------------------------------------------------------
# Test 13c: comment - 同一秒の連続送信でも sent/ のファイルが上書きされないこと (RV064 P3-2)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t13c")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

cd "${TEST_ENV}/worktree"
# 1回目の送信
cat <<'EOF' > "${DRAFT_PATH}"
# Draft 1
- Role: reviewer
- Round: 1
- Status: Changes requested
## Summary
first draft
EOF
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer

# 2回目の送信（直後に実行）
cat <<'EOF' > "${DRAFT_PATH}"
# Draft 2
- Role: reviewer
- Round: 2
- Status: Changes requested
## Summary
second draft
EOF
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer

SENT_COUNT=$(find "${TEST_ENV}/worktree/.input/review/sent" -name "*-reviewer-draft*.md" | wc -l)
if [[ "${SENT_COUNT}" -eq 2 ]]; then
  pass "Archive both files to sent/ without overwriting (RV064 P3-2)"
else
  fail "Archive both files to sent/ without overwriting (RV064 P3-2)" "Count: ${SENT_COUNT}"
fi

# ---------------------------------------------------------------------------
# Test 14: comment-issue - 監査 Issue へのコメントと退避
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t14")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 1
- Status: Approved
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment-issue reviewer 99

if grep -q "<!-- review-sync role=reviewer round=1 -->" "${LAST_BODY}"; then
  pass "Inject marker on comment-issue"
else
  fail "Inject marker on comment-issue" "Marker not found"
fi

if [[ ! -f "${DRAFT_PATH}" ]] && compgen -G "${TEST_ENV}/worktree/.input/review/sent/*-reviewer-draft.md" >/dev/null; then
  pass "Archive draft on comment-issue success"
else
  fail "Archive draft on comment-issue success" "Draft not moved"
fi

# ---------------------------------------------------------------------------
# Test 15: pull - 相手マーカー付き投稿の抽出
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t15")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
if [[ "$*" == *"pr view --json number,url"* ]]; then
  echo '{"number": 5}'
  exit 0
elif [[ "$*" == *"pr view 5 --json comments,reviews"* ]]; then
  cat <<'JSON'
{
  "comments": [
    {
      "body": "<!-- review-sync role=reviewer round=1 -->\n# Review Draft: PR #5\n- Role: reviewer\n- Round: 1\n- Status: Changes requested\n## Findings\n### F001 [High] Issue Title\n- Status: open\n",
      "createdAt": "2026-10-28T10:00:00Z"
    }
  ],
  "reviews": []
}
JSON
  exit 0
elif [[ "$*" == *"repo view"* ]]; then
  echo "example/repo"
  exit 0
elif [[ "$*" == *"pulls/5/comments"* ]]; then
  echo "[]"
  exit 0
fi
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewee

PULLED_DRAFT="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"
if grep -q "F001 \[High\] Issue Title" "${PULLED_DRAFT}" && grep -q -- "- Role: reviewee" "${PULLED_DRAFT}" && grep -q -- "- Round: 1" "${PULLED_DRAFT}"; then
  pass "Pull extracts reviewer finding and sets reviewee role and round"
else
  fail "Pull extracts reviewer finding" "Content: $(cat "${PULLED_DRAFT}" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Test 16: pull - 人間・インラインコメントの取り込み
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t16")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
if [[ "$*" == *"pr view --json number,url"* ]]; then
  echo '{"number": 7}'
  exit 0
elif [[ "$*" == *"pr view 7 --json comments,reviews"* ]]; then
  cat <<'JSON'
{
  "comments": [
    {
      "body": "Here is a manual comment from developer",
      "createdAt": "2026-10-28T10:05:00Z",
      "author": {"login": "human-dev"}
    }
  ],
  "reviews": []
}
JSON
  exit 0
elif [[ "$*" == *"repo view"* ]]; then
  echo "example/repo"
  exit 0
elif [[ "$*" == *"pulls/7/comments"* ]]; then
  cat <<'JSON'
[
  {
    "path": "app/models.py",
    "line": 42,
    "body": "Please add index here",
    "created_at": "2026-10-28T10:06:00Z",
    "user": {"login": "human-reviewer"}
  }
]
JSON
  exit 0
fi
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewer

PULLED_DRAFT="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
if grep -q "Here is a manual comment from developer" "${PULLED_DRAFT}" && grep -q "\[app/models.py:42\] Please add index here" "${PULLED_DRAFT}"; then
  pass "Pull captures human and inline comments section"
else
  fail "Pull captures human and inline comments section" "Content: $(cat "${PULLED_DRAFT}" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Test 17: pull - gh 失敗時に非0で停止しドラフトを作らないこと
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t17")
cd "${TEST_ENV}/worktree"

ERR_OUTPUT=$(MOCK_PR_VIEW_FAIL=1 PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewee 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"取得に失敗しました"* ]] && [[ ! -f "${TEST_ENV}/worktree/.input/review/reviewee-draft.md" ]]; then
  pass "Pull stops with non-zero and avoids creating empty draft on gh failure"
else
  fail "Pull stops with non-zero on gh failure" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 17b: pull - gh が stderr に警告を出力しても stdout の JSON が安全にパースされること (RV064 P3-4)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t17b")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
if [[ "$*" == *"pr view --json number,url"* ]]; then
  echo "WARNING: gh update available" >&2
  echo '{"number": 99}'
  exit 0
elif [[ "$*" == *"pr view 99 --json comments,reviews"* ]]; then
  echo "WARNING: gh token expires soon" >&2
  cat <<'JSON'
{
  "comments": [
    {
      "body": "<!-- review-sync role=reviewer round=1 -->\n# Review Draft: PR #99\n- Role: reviewer\n- Round: 1\n- Status: Changes requested\n## Findings\n### F001 [High] Issue Title\n",
      "createdAt": "2026-10-28T10:00:00Z"
    }
  ],
  "reviews": []
}
JSON
  exit 0
elif [[ "$*" == *"repo view"* ]]; then
  echo "WARNING: git config notice" >&2
  echo "example/repo"
  exit 0
elif [[ "$*" == *"pulls/99/comments"* ]]; then
  echo "WARNING: api pagination note" >&2
  echo "[]"
  exit 0
fi
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewee

PULLED_DRAFT="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"
if [[ -f "${PULLED_DRAFT}" ]] && grep -q "F001 \[High\] Issue Title" "${PULLED_DRAFT}"; then
  pass "Pull succeeds even when gh outputs warnings to stderr (RV064 P3-4)"
else
  fail "Pull succeeds even when gh outputs warnings to stderr (RV064 P3-4)" "Content: $(cat "${PULLED_DRAFT}" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Test 18: load_github_auth - 認証スクリプト stdout の GH_TOKEN へのエクスポート伝搬
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t18")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > scripts/lib/github_app_auth.py
#!/usr/bin/env python3
import sys
sys.stdout.write("ghs_mock_token_from_script\n")
sys.exit(0)
EOF
chmod +x scripts/lib/github_app_auth.py

LOADED_TOKEN=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash -c '
REPO_ROOT="$(pwd)"
source <(sed -n "/^load_github_auth()/,/^}/p" scripts/review-sync.sh)
load_github_auth
echo "${GH_TOKEN:-}"
')

if [[ "${LOADED_TOKEN}" == *"ghs_mock_token_from_script"* ]]; then
  pass "Propagate auth script stdout to GH_TOKEN (RV064 P3-3)"
else
  fail "Propagate auth script stdout to GH_TOKEN (RV064 P3-3)" "Token: ${LOADED_TOKEN}"
fi

# ---------------------------------------------------------------------------
# Test 19: load_github_auth - 本物の github_app_auth.py と .env.github-app 結合テスト (RV064 P3-3)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t19")
cd "${TEST_ENV}/worktree"

# ダミー RSA 秘密鍵の作成
openssl genrsa -out dummy_key.pem 2048 2>/dev/null

cat <<'EOF' > .env.github-app
GITHUB_BOT_APP_ID=111111
GITHUB_BOT_INSTALL_ID=222222
GITHUB_APP_PRIVATE_KEY_PATH=./dummy_key.pem
EOF

LOADED_TOKEN=$(PYTHONPATH="${TEST_ENV}/worktree" python3 -c '
import os, sys, unittest.mock as mock
from scripts.lib import github_app_auth

with mock.patch("scripts.lib.github_app_auth.fetch_installation_token", return_value="ghs_real_lib_integration_token"):
    res = github_app_auth.obtain_token(".")
    assert res.token == "ghs_real_lib_integration_token", f"Token: {res.token}, Error: {res.error}"
    print(res.token)
' 2>/dev/null || true)

if [[ "${LOADED_TOKEN}" == *"ghs_real_lib_integration_token"* ]]; then
  pass "Integration test: Real github_app_auth reads .env.github-app (RV064 P3-3)"
else
  fail "Integration test: Real github_app_auth reads .env.github-app (RV064 P3-3)" "Token: ${LOADED_TOKEN}"
fi

# ---------------------------------------------------------------------------
# Test 19b: load_github_auth - 本物の github_app_auth.py と環境変数 結合テスト (RV064 P3-3)
# ---------------------------------------------------------------------------
rm -f .env.github-app
LOADED_ENV_TOKEN=$(GITHUB_BOT_APP_ID=333333 GITHUB_BOT_INSTALL_ID=444444 GITHUB_APP_PRIVATE_KEY_PATH=./dummy_key.pem \
  PYTHONPATH="${TEST_ENV}/worktree" python3 -c '
import os, sys, unittest.mock as mock
from scripts.lib import github_app_auth

with mock.patch("scripts.lib.github_app_auth.fetch_installation_token", return_value="ghs_real_lib_env_token"):
    res = github_app_auth.obtain_token(".")
    assert res.token == "ghs_real_lib_env_token", f"Token: {res.token}, Error: {res.error}"
    print(res.token)
' 2>/dev/null || true)

if [[ "${LOADED_ENV_TOKEN}" == *"ghs_real_lib_env_token"* ]]; then
  pass "Integration test: Real github_app_auth reads env vars without file (RV064 P3-3)"
else
  fail "Integration test: Real github_app_auth reads env vars without file (RV064 P3-3)" "Token: ${LOADED_ENV_TOKEN}"
fi

# ---------------------------------------------------------------------------
# Test 20: load_github_auth - Bot 設定ありでトークン取得失敗時は個人認証へフォールバックせずエラー停止
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t20")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > scripts/lib/github_app_auth.py
#!/usr/bin/env python3
import sys
sys.stderr.write("ERROR: 秘密鍵ファイルが見つかりません: /nonexistent.pem\n")
sys.exit(1)
EOF
chmod +x scripts/lib/github_app_auth.py

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
if [[ "$*" == *"auth status"* ]]; then
  exit 0
fi
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

ERR_OUTPUT=$(GITHUB_BOT_APP_ID=111111 GITHUB_BOT_INSTALL_ID=222222 GITHUB_APP_PRIVATE_KEY_PATH=/nonexistent.pem PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash -c '
REPO_ROOT="$(pwd)"
source <(sed -n "/^load_github_auth()/,/^}/p" scripts/review-sync.sh)
load_github_auth
' 2>&1 || true)

if [[ "${ERR_OUTPUT}" == *"秘密鍵ファイルが見つかりません"* ]]; then
  pass "Bot config failure stops immediately without falling back to personal auth"
else
  fail "Bot config failure stops immediately without falling back to personal auth" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 21: load_github_auth - 認証情報が一切ない場合に個人認証を要求してエラー停止
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t21")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
if [[ "$*" == *"auth status"* ]]; then
  exit 1
fi
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

ERR_OUTPUT=$(env -u GH_TOKEN -u GITHUB_TOKEN -u GITHUB_BOT_APP_ID -u GITHUB_APP_ID -u GITHUB_BOT_INSTALL_ID -u GITHUB_APP_INSTALLATION_ID -u GITHUB_APP_PRIVATE_KEY_PATH PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash -c '
REPO_ROOT="$(pwd)"
source <(sed -n "/^load_github_auth()/,/^}/p" scripts/review-sync.sh)
load_github_auth
' 2>&1 || true)

if [[ "${ERR_OUTPUT}" == *"認証情報が見つかりません"* ]]; then
  pass "Stop with clear error when no auth credentials found"
else
  fail "Stop with clear error when no auth credentials found" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 22: pull - インラインコメント取得失敗時に非0で停止すること
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t22")
cd "${TEST_ENV}/worktree"

ERR_OUTPUT=$(MOCK_INLINE_COMMENTS_FAIL=1 PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewee 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"インラインコメント取得に失敗しました"* ]]; then
  pass "Pull stops with non-zero when inline comments API fails"
else
  fail "Pull stops with non-zero when inline comments API fails" "Output: ${ERR_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 23: pr-create - Bot トークンを利用して gh pr create を透過呼び出しすること
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t23")
cd "${TEST_ENV}/worktree"

# モック github_app_auth.py (トークン出力)
cat <<'EOF' > "${TEST_ENV}/worktree/scripts/lib/github_app_auth.py"
import sys
print("ghs_mock_bot_token_for_pr_create")
sys.exit(0)
EOF

MOCK_LOG="${TEST_ENV}/gh.log"
OUTPUT=$(env -u GH_TOKEN -u GITHUB_TOKEN MOCK_GH_LOG="${MOCK_LOG}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pr-create --title "Test PR" --body "Test Body" 2>&1 || true)

if grep -q "mock-pr-create: GH_TOKEN=ghs_mock_bot_token_for_pr_create args=pr create --title Test PR --body Test Body" "${MOCK_LOG}" 2>/dev/null; then
  pass "pr-create sets GH_TOKEN to bot token and forwards arguments to gh pr create"
else
  fail "pr-create sets GH_TOKEN to bot token" "Log: $(cat "${MOCK_LOG}" 2>/dev/null || echo 'no log'), Output: ${OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 24: pr-create - GitHub App 設定なし時に警告を出力すること
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t24")
cd "${TEST_ENV}/worktree"

# トークンなし（個人認証モード）
cat <<'EOF' > "${TEST_ENV}/worktree/scripts/lib/github_app_auth.py"
import sys
sys.exit(0)
EOF

MOCK_LOG="${TEST_ENV}/gh.log"
OUTPUT=$(env -u GH_TOKEN -u GITHUB_TOKEN MOCK_GH_LOG="${MOCK_LOG}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pr-create --title "Personal PR" 2>&1 || true)

if [[ "${OUTPUT}" == *"警告: GitHub App 設定が存在しません"* ]] && grep -q "mock-pr-create: GH_TOKEN=none args=pr create --title Personal PR" "${MOCK_LOG}" 2>/dev/null; then
  pass "pr-create warns when GitHub App config is absent and falls back to personal auth"
else
  fail "pr-create warns when GitHub App config is absent" "Output: ${OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 25: Python 認証モジュール・ドラフト生成モジュールの単体テスト実行
# ---------------------------------------------------------------------------
if (cd "${SCRIPT_DIR}" && python3 -m unittest tests/test_github_app_auth.py tests/test_pull_review.py) >/dev/null 2>&1; then
  pass "Python modules unit tests passed (github_app_auth, pull_review)"
else
  fail "Python modules unit tests" "unittest failed on tests/test_github_app_auth.py / tests/test_pull_review.py"
fi

echo "=========================================="
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [[ "${FAILED}" -gt 0 ]]; then
  exit 1
fi
exit 0
