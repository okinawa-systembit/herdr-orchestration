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
  for arg in "$@"; do
    if [[ -f "$arg" ]]; then
      cat "$arg" > "${MOCK_LAST_BODY:-/dev/null}"
    fi
  done
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
ERR_MSG_BYPASS=$(ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee 2>&1 || true)
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
ERR_OUTPUT=$(MOCK_GH_LOG="${MOCK_LOG}" MOCK_PR_NOT_FOUND=1 PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved 2>&1 || true)
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
## Summary
test summary
EOF

# comment reviewer を実行（内部で resolve_pr_number が呼ばれる）
PR_OUTPUT=$(MOCK_GH_LOG="${MOCK_LOG}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status "Changes requested" --round 1 2>&1 || true)
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
Token: ghs_12345678901234567890123456789012
EOF

cd "${TEST_ENV}/worktree"
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer --status "Changes requested" 2>&1 || true)
if [[ "${ERR_OUTPUT}" == *"機密情報"* ]]; then
  pass "Detect leaked ghs_ token in check"
else
  fail "Detect leaked ghs_ token in check" "Output: ${ERR_OUTPUT}"
fi

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
EOF
ERR_OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer --status "Changes requested" 2>&1 || true)
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
# Test 12a: check - reviewee 本文中の自由記述（プレースホルダー等含む）は機械検査で停止せず通過すること (指針8・9 / Issue #116)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12a")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Findings
### P1-1 Title
- Decision: <accept | reject | defer>
- レビューイ評価: 同意
EOF

cd "${TEST_ENV}/worktree"
OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee)
if [[ "${OUT}" == *"OK: check passed (reviewee)"* ]]; then
  pass "Pass reviewee draft containing template placeholders in freeform body (Issue #116)"
else
  fail "Pass reviewee draft containing template placeholders in freeform body" "Output: ${OUT}"
fi

# ---------------------------------------------------------------------------
# Test 12b: check - reviewee 本文の Decision 値は過剰な機械バリデーションを行わず通過すること (指針8・9 / Issue #116)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12b")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Findings
### P1-1 Title
- Decision: 自由記述の回答
- レビューイ評価: 同意
EOF

cd "${TEST_ENV}/worktree"
OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee)
if [[ "${OUT}" == *"OK: check passed (reviewee)"* ]]; then
  pass "Pass reviewee draft without mechanical validation on body decision values (Issue #116)"
else
  fail "Pass reviewee draft without mechanical validation on body decision values" "Output: ${OUT}"
fi

# ---------------------------------------------------------------------------
# Test 12c: check - レビュワー本文中の説明文に目印文字列があっても欄記入済みなら通過 (RV080 P2-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12c")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Findings
### P2-1 レビュワーによる指摘
本文中で「初期値を <accept | reject | defer> のような目印にする」と書かれている例
- Decision: accept
- レビューイ評価: 同意
- 対応内容 / 理由: 修正完了
EOF

cd "${TEST_ENV}/worktree"
OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee)
if [[ "${OUT}" == *"OK: check passed (reviewee)"* ]]; then
  pass "Pass reviewee draft when placeholder text is only in body description (RV080 P2-1)"
else
  fail "Pass reviewee draft when placeholder text is only in body description" "Output: ${OUT}"
fi

# ---------------------------------------------------------------------------
# Test 12d: check - バッククォート付きの Decision 値 (`accept` 等) を許容 (RV080 P3-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12d")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Findings
### P1-1 Title
- Decision: `accept`
- レビューイ評価: `同意`
EOF

cd "${TEST_ENV}/worktree"
OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee)
if [[ "${OUT}" == *"OK: check passed (reviewee)"* ]]; then
  pass "Pass reviewee draft with backquoted Decision value (RV080 P3-1)"
else
  fail "Pass reviewee draft with backquoted Decision value" "Output: ${OUT}"
fi

# ---------------------------------------------------------------------------
# Test 12e: check - レビューイ評価項目の省略・空欄も自由記述として通過すること (指針8・9 / Issue #116)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12e")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Findings
### P1-1 Title
- Decision: accept
- レビューイ評価:
EOF

cd "${TEST_ENV}/worktree"
OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee)
if [[ "${OUT}" == *"OK: check passed (reviewee)"* ]]; then
  pass "Pass reviewee draft with omitted evaluation field (Issue #116)"
else
  fail "Pass reviewee draft with omitted evaluation field" "Output: ${OUT}"
fi

# ---------------------------------------------------------------------------
# Test 12f: check - コードブロックやチルダが混在するドラフトでも正常通過すること (Issue #110 / #113)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t12f")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 1
- Status: Approved
## Summary
```markdown
コードブロック内の引用:
~~~bash
echo "nested"
~~~
```
EOF

cd "${TEST_ENV}/worktree"
OUTPUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer --status Approved 2>&1 || true)
if [[ "${OUTPUT}" == *"OK: check passed (reviewer)"* ]]; then
  pass "Pass draft with mixed code blocks without mechanical parse error (Issue #110 / #113)"
else
  fail "Pass draft with mixed code blocks without mechanical parse error" "Output: ${OUTPUT}"
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
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 1 --status "Changes requested" --round 2

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
# Test 13b: comment - --round CLI 指定によるマーカー付与
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t13b")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 1 --status "Changes requested" --round 2

if grep -q "^<!-- review-sync role=reviewer round=2 -->$" "${LAST_BODY}"; then
  pass "Set numeric round marker via --round CLI option"
else
  fail "Set numeric round marker via --round CLI option" "Marker: $(head -n 2 "${LAST_BODY}")"
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
## Summary
first draft
EOF
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 1 --status "Changes requested" --round 1

# 2回目の送信（直後に実行）
cat <<'EOF' > "${DRAFT_PATH}"
# Draft 2
- Role: reviewer
## Summary
second draft
EOF
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 1 --status "Changes requested" --round 2

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
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment-issue reviewer 99 --status Approved --round 1

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
# Test 18: load_github_auth & run_gh - トークンの export 廃止と run_gh でのインライン渡し (原則 5 / Issue #116 / RV092 P3-1, P3-2)
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

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
echo "gh called with GH_TOKEN=${GH_TOKEN:-none} args=$*"
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

TEST_RESULT=$(env _REVIEW_SYNC_AUTH_TOKEN="preexisting_exported" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash -c '
REPO_ROOT="$(pwd)"
TMP_DIR="${REPO_ROOT}/.input/tmp"
mkdir -p "${TMP_DIR}"
source <(sed -n "/^create_temp_file()/,/^}/p" scripts/review-sync.sh)
source <(sed -n "/^# 3. GitHub 認証情報/,/^BOT_AUTH_ACTIVE=0/p" scripts/review-sync.sh)
source <(sed -n "/^run_gh()/,/^}/p" scripts/review-sync.sh)
source <(sed -n "/^load_github_auth()/,/^}/p" scripts/review-sync.sh)
load_github_auth

# 1. スクリプトの環境変数に GH_TOKEN が export されていないこと（原則 5）
if [[ -n "${GH_TOKEN:-}" ]]; then
  echo "FAIL_LEAKED_GH_TOKEN=${GH_TOKEN}"
  exit 1
fi

# 2. P3-1, P3-2: 子プロセス (env) を実行しても、環境変数に取得トークンが一切出現しないこと
CHILD_ENV=$(env)
if echo "${CHILD_ENV}" | grep -q "ghs_mock_token_from_script"; then
  echo "FAIL_TOKEN_LEAKED_TO_CHILD_ENV"
  exit 1
fi

# 3. run_gh 経由でのみインラインで GH_TOKEN が渡されること（原則 5）
run_gh auth status
')

if [[ "${TEST_RESULT}" == *"gh called with GH_TOKEN=ghs_mock_token_from_script args=auth status"* ]] && [[ "${TEST_RESULT}" != *"FAIL_"* ]]; then
  pass "Keep token private to AUTH_TOKEN and pass inline via run_gh without exporting to environment (RV064 P3-3 / Issue #116 / RV092 P3-1, P3-2)"
else
  fail "Keep token private to AUTH_TOKEN and pass inline via run_gh" "Output: ${TEST_RESULT}"
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
TMP_DIR="${REPO_ROOT}/.input/tmp"
mkdir -p "${TMP_DIR}"
source <(sed -n "/^create_temp_file()/,/^}/p" scripts/review-sync.sh)
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
TMP_DIR="${REPO_ROOT}/.input/tmp"
mkdir -p "${TMP_DIR}"
source <(sed -n "/^create_temp_file()/,/^}/p" scripts/review-sync.sh)
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

if grep -q "mock-pr-create: GH_TOKEN=ghs_mock_bot_token_for_pr_create args=pr create --title Test PR --body" "${MOCK_LOG}" 2>/dev/null; then
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

# ---------------------------------------------------------------------------
# Test 26: pr-create - --body を .input/tmp/ 経由で自動整形し --body-file へ変換
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t26")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/worktree/scripts/lib/github_app_auth.py"
import sys
sys.exit(0)
EOF

MOCK_LOG="${TEST_ENV}/gh.log"
OUTPUT=$(env -u GH_TOKEN -u GITHUB_TOKEN MOCK_GH_LOG="${MOCK_LOG}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pr-create --title "Format PR" --body "## Title
- item 1
- item 2" 2>&1 || true)

if grep -q "mock-pr-create: GH_TOKEN=none args=pr create --title Format PR --body-file ${TEST_ENV}/worktree/.input/tmp/pr-body." "${MOCK_LOG}" 2>/dev/null; then
  pass "pr-create formats body via .input/tmp/ temporary file"
else
  fail "pr-create formats body via .input/tmp/ temporary file" "Log: $(cat "${MOCK_LOG}" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Test 27: comment - .prettierignore があっても .input/ 内のドラフトが自動整形されること (RV082 P2-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t27")
cd "${TEST_ENV}/worktree"
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

# .prettierignore に .input/ が指定されている環境を再現
cat <<'EOF' > "${TEST_ENV}/worktree/.prettierignore"
.input/
EOF

# 未整形の Markdown (アスタリスク箇条書きと過剰スペース)
cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 1
- Status: Approved
## Summary
*  unformatted item 1
*  unformatted item 2
EOF

MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 1

SENT_FILE=$(compgen -G "${TEST_ENV}/worktree/.input/review/sent/*-reviewer-draft.md" | head -n 1 || true)
if [[ -f "${SENT_FILE}" ]] && grep -q -- "- unformatted item 1" "${SENT_FILE}"; then
  pass "Format markdown in .input/ bypassing .prettierignore (RV082 P2-1)"
else
  fail "Format markdown in .input/ bypassing .prettierignore" "Sent content: $(cat "${SENT_FILE}" 2>/dev/null || echo 'not found')"
fi

# ---------------------------------------------------------------------------
# Test 28: format - 悪意ある prettier.config.mjs が存在しても実行されないこと (RV082 P2-2)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t28")
cd "${TEST_ENV}/worktree"
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
MARKER_FILE="${TEST_ENV}/exploit_marker.txt"

# 悪意ある prettier.config.mjs (実行されたら印ファイルを生成)
cat <<EOF > "${TEST_ENV}/worktree/prettier.config.mjs"
import fs from 'node:fs';
fs.writeFileSync('${MARKER_FILE}', 'EXPLOIT_EXECUTED');
export default {};
EOF

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewer
- Round: 1
- Status: Approved
## Summary
summary text
EOF

PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer >/dev/null 2>&1 || true

if [[ ! -f "${MARKER_FILE}" ]]; then
  pass "Do not execute repository prettier.config.mjs during format (RV082 P2-2)"
else
  fail "Do not execute repository prettier.config.mjs during format" "Marker file was created"
fi

# ---------------------------------------------------------------------------
# Test 29: pr-create - --body-file の元ファイルを保護し一時ファイルを残存させないこと (RV082 P3-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t29")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/worktree/scripts/lib/github_app_auth.py"
import sys
sys.exit(0)
EOF

ORIGINAL_BODY_FILE="${TEST_ENV}/worktree/my-pr-body.md"
ORIGINAL_CONTENT="*   original raw content   *"
printf '%s\n' "${ORIGINAL_CONTENT}" > "${ORIGINAL_BODY_FILE}"

MOCK_LOG="${TEST_ENV}/gh.log"
OUTPUT=$(env -u GH_TOKEN -u GITHUB_TOKEN MOCK_GH_LOG="${MOCK_LOG}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pr-create --title "Safe PR" --body-file "${ORIGINAL_BODY_FILE}" 2>&1 || true)

# 1. 元ファイルが変更されていないこと
CURRENT_CONTENT="$(cat "${ORIGINAL_BODY_FILE}")"
# 2. 一時ファイルが .input/tmp/ に残存していないこと
REMAINING_TMP_COUNT=$(find "${TEST_ENV}/worktree/.input/tmp" -type f | wc -l)

if [[ "${CURRENT_CONTENT}" == "${ORIGINAL_CONTENT}" ]] && [[ "${REMAINING_TMP_COUNT}" -eq 0 ]]; then
  pass "Protect original body-file and clean up temporary files on pr-create (RV082 P3-1)"
else
  fail "Protect original body-file and clean up temporary files" "Content changed: $(( CURRENT_CONTENT != ORIGINAL_CONTENT )), Remaining tmp files: ${REMAINING_TMP_COUNT}"
fi

# ---------------------------------------------------------------------------
# Test 30: pr-create - --body-file - で標準入力から本文を受け取り整形して渡すこと (RV083 P3-1 / RV085 P3-3 / Issue #110)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t30")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/worktree/scripts/lib/github_app_auth.py"
import sys
sys.exit(0)
EOF

MOCK_LOG="${TEST_ENV}/gh.log"
MOCK_BODY="${TEST_ENV}/gh-body.txt"
OUTPUT=$(printf '*  stdin item 1\n*  stdin item 2\n' | env -u GH_TOKEN -u GITHUB_TOKEN MOCK_GH_LOG="${MOCK_LOG}" MOCK_LAST_BODY="${MOCK_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pr-create --title "Stdin PR" --body-file - 2>&1 || true)

REMAINING_TMP_COUNT=$(find "${TEST_ENV}/worktree/.input/tmp" -type f | wc -l)
CAPTURED_BODY="$(cat "${MOCK_BODY}" 2>/dev/null || true)"

if grep -q "mock-pr-create: GH_TOKEN=none args=pr create --title Stdin PR --body-file ${TEST_ENV}/worktree/.input/tmp/pr-body-copy." "${MOCK_LOG}" 2>/dev/null \
  && [[ "${REMAINING_TMP_COUNT}" -eq 0 ]] \
  && [[ "${CAPTURED_BODY}" == *"- stdin item 1"* ]] \
  && [[ "${CAPTURED_BODY}" != *"*  stdin item 1"* ]]; then
  pass "Accept --body-file - from stdin, format with prettier, and clean up temp files (RV083 P3-1 / RV085 P3-3 / Issue #110)"
else
  fail "Accept --body-file - from stdin" "Log: $(cat "${MOCK_LOG}" 2>/dev/null || true), Remaining tmp: ${REMAINING_TMP_COUNT}, Body: ${CAPTURED_BODY}, Output: ${OUTPUT}"
fi

# ---------------------------------------------------------------------------
# Test 31: check/comment - reviewer の --status CLI 指定と外側ヘッダー優先付与 (RV095 P1-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t31")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"

# 1. ドラフトに Status 行がなくても --status Approved で通過し付与されること
cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft: PR #118
- Role: reviewer
- Round: 1
## Summary
Review summary without status line in body.
EOF

cd "${TEST_ENV}/worktree"
CHECK_OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer --status Approved 2>&1 || true)
if [[ "${CHECK_OUT}" == *"OK: check passed (reviewer)"* ]]; then
  pass "Pass check reviewer with --status Approved when body lacks Status (Issue #119)"
else
  fail "Pass check reviewer with --status Approved when body lacks Status" "Output: ${CHECK_OUT}"
fi

# 2. ドラフトに - Status: Changes requested があっても、--status Approved で送信すると Approved だけが投稿されること (P1-1 回帰防止)
cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft: PR #118
- Role: reviewer
- Round: 1
- Status: Changes requested
## Summary
Review summary.
EOF

MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 1
CAPTURED_SUBMITTED="$(cat "${LAST_BODY}" 2>/dev/null || true)"
STATUS_COUNT=$(grep -c "^- Status:" "${LAST_BODY}" || true)
if [[ "${CAPTURED_SUBMITTED}" == *"- Status: Approved"* ]] && [[ "${CAPTURED_SUBMITTED}" != *"- Status: Changes requested"* ]] && [[ "${STATUS_COUNT}" -eq 1 ]]; then
  pass "Override body Status with CLI --status Approved and avoid duplicate Status lines (RV095 P1-1)"
else
  fail "Override body Status with CLI --status Approved" "Count: ${STATUS_COUNT}, Body: ${CAPTURED_SUBMITTED}"
fi

# 3. 逆の組み合わせ: ドラフトに - Status: Approved があっても、--status 'Changes requested' で送信すると Changes requested だけが投稿されること (P1-1 回帰防止)
cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft: PR #118
- Role: reviewer
- Round: 1
- Status: Approved
## Summary
Review summary.
EOF

MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status "Changes requested" --round 1
CAPTURED_SUBMITTED2="$(cat "${LAST_BODY}" 2>/dev/null || true)"
STATUS_COUNT2=$(grep -c "^- Status:" "${LAST_BODY}" || true)
if [[ "${CAPTURED_SUBMITTED2}" == *"- Status: Changes requested"* ]] && [[ "${CAPTURED_SUBMITTED2}" != *"- Status: Approved"* ]] && [[ "${STATUS_COUNT2}" -eq 1 ]]; then
  pass "Override body Status with CLI --status 'Changes requested' (RV095 P1-1)"
else
  fail "Override body Status with CLI --status 'Changes requested'" "Count: ${STATUS_COUNT2}, Body: ${CAPTURED_SUBMITTED2}"
fi

# 4. ドラフトに旧 Status 行が残っていても、check は --status のみ検証して通過すること (指針9)
cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft: PR #118
- Role: reviewer
- Round: 1
- Status: Changes requested
## Summary
Review summary.
EOF

CHECK_PASS=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer --status "Changes requested" 2>&1 || true)
if [[ "${CHECK_PASS}" == *"OK: check passed (reviewer)"* ]]; then
  pass "Pass check reviewer regardless of legacy Status in body (RV095 P1-1 (a))"
else
  fail "Pass check reviewer regardless of legacy Status in body" "Output: ${CHECK_PASS}"
fi

# ---------------------------------------------------------------------------
# Test 32: comment - state.json からの Round 解決と target_id 照合 (RV095 P2-1)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t32")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"
STATE_FILE="${TEST_ENV}/worktree/.input/review/state.json"

# モックの PR 番号は 2 なので、一致する target_id: "2" を設定
cat <<'EOF' > "${STATE_FILE}"
{
  "reviewer": {
    "round": 3,
    "target_id": "2",
    "target_type": "pr"
  }
}
EOF

cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft
- Role: reviewer
## Summary
Draft body with absolutely no Round line.
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status "Changes requested"
if grep -q "<!-- review-sync role=reviewer round=3 -->" "${LAST_BODY}"; then
  pass "Resolve round from state.json when target_id matches (RV095 P2-1)"
else
  fail "Resolve round from state.json when target_id matches" "Body: $(cat "${LAST_BODY}" 2>/dev/null || true)"
fi

if grep -q '"last_sent_round": 3' "${STATE_FILE}"; then
  pass "Update state.json with last_sent_round on comment success (Issue #119)"
else
  fail "Update state.json with last_sent_round on comment success" "State: $(cat "${STATE_FILE}" 2>/dev/null || true)"
fi

# target_id が不一致の場合、エラー停止すること (P2-1 回帰防止)
cat <<'EOF' > "${STATE_FILE}"
{
  "reviewer": {
    "round": 7,
    "target_id": "999",
    "target_type": "pr"
  }
}
EOF
cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft
- Role: reviewer
## Summary
Draft body.
EOF

MISMATCH_ERR=$(MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status "Changes requested" 2>&1 || true)
if [[ "${MISMATCH_ERR}" == *"state.json の対象"* && "${MISMATCH_ERR}" == *"一致しません"* ]]; then
  pass "Stop with clear error when state.json target_id mismatches destination (RV095 P2-1)"
else
  fail "Stop with clear error when state.json target_id mismatches destination" "Output: ${MISMATCH_ERR}"
fi

# ---------------------------------------------------------------------------
# Test 33: comment - --round CLI 指定による明示的オーバーライド (Issue #119)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t33")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
LAST_BODY="${TEST_ENV}/last_body.txt"
STATE_FILE="${TEST_ENV}/worktree/.input/review/state.json"

cat <<'EOF' > "${STATE_FILE}"
{
  "reviewer": {
    "round": 2,
    "target_id": "2",
    "target_type": "pr"
  }
}
EOF

cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft
- Role: reviewer
## Summary
Draft body.
EOF

cd "${TEST_ENV}/worktree"
MOCK_LAST_BODY="${LAST_BODY}" PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved --round 5
if grep -q "<!-- review-sync role=reviewer round=5 -->" "${LAST_BODY}"; then
  pass "Override round via --round CLI argument (Issue #119)"
else
  fail "Override round via --round CLI argument" "Body: $(cat "${LAST_BODY}" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Test 34: check - reviewee への --status 指定は拒否 (Issue #119)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t34")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Draft
- Role: reviewee
- Round: 1
## Summary
summary text
EOF

cd "${TEST_ENV}/worktree"
ERR_OUT=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewee --status Approved 2>&1 || true)
if [[ "${ERR_OUT}" == *"reviewee に --status を指定することはできません"* ]]; then
  pass "Reject --status argument for reviewee (Issue #119)"
else
  fail "Reject --status argument for reviewee" "Output: ${ERR_OUT}"
fi

# ---------------------------------------------------------------------------
# Test 35: pull - state.json および incoming.md の生成と incoming/outgoing 分離 (Issue #119)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t35")
cd "${TEST_ENV}/worktree"

cat <<'EOF' > "${TEST_ENV}/bin/gh"
#!/usr/bin/env bash
if [[ "$*" == *"pr view --json number,url"* ]]; then
  echo '{"number": 42}'
  exit 0
elif [[ "$*" == *"pr view 42 --json comments,reviews"* ]]; then
  cat <<'JSON'
{
  "comments": [
    {
      "body": "<!-- review-sync role=reviewer round=2 -->\n# Review PR #42\n- Role: reviewer\n- Round: 2\n- Status: Changes requested\n## Summary\nPlease address findings.\n",
      "createdAt": "2026-11-04T12:00:00Z"
    }
  ],
  "reviews": []
}
JSON
  exit 0
elif [[ "$*" == *"repo view"* ]]; then
  echo "example/repo"
  exit 0
elif [[ "$*" == *"pulls/42/comments"* ]]; then
  echo "[]"
  exit 0
fi
exit 0
EOF
chmod +x "${TEST_ENV}/bin/gh"

PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh pull reviewee

PULLED_DRAFT="${TEST_ENV}/worktree/.input/review/reviewee-draft.md"
INCOMING_FILE="${TEST_ENV}/worktree/.input/review/incoming.md"
STATE_FILE="${TEST_ENV}/worktree/.input/review/state.json"

if [[ -f "${INCOMING_FILE}" ]] && grep -q "Please address findings." "${INCOMING_FILE}" && ! grep -q "<!-- review-sync" "${INCOMING_FILE}"; then
  pass "Generate incoming.md without raw marker on pull (Issue #119)"
else
  fail "Generate incoming.md without raw marker on pull" "Incoming: $(cat "${INCOMING_FILE}" 2>/dev/null || true)"
fi

if [[ -f "${STATE_FILE}" ]] && grep -q '"round": 2' "${STATE_FILE}" && grep -q '"target_id": "42"' "${STATE_FILE}"; then
  pass "Generate state.json with round and target_id on pull (Issue #119)"
else
  fail "Generate state.json with round and target_id on pull" "State: $(cat "${STATE_FILE}" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Test 36: state.json - 不正な round 値 (非整数) の場合はエラー停止 (RV095 P3-2)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t36")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"
STATE_FILE="${TEST_ENV}/worktree/.input/review/state.json"

cat <<'EOF' > "${STATE_FILE}"
{
  "reviewer": {
    "round": "1 --> injected <!--",
    "target_id": "2",
    "target_type": "pr"
  }
}
EOF

cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft
- Role: reviewer
## Summary
Draft body.
EOF

cd "${TEST_ENV}/worktree"
INJECT_ERR=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status "Changes requested" 2>&1 || true)
if [[ "${INJECT_ERR}" == *"round 値が無効です"* ]]; then
  pass "Reject invalid non-numeric round from state.json (RV095 P3-2)"
else
  fail "Reject invalid non-numeric round from state.json" "Output: ${INJECT_ERR}"
fi

# ---------------------------------------------------------------------------
# Test 37: check コマンドでの --round オプション拒否および本文読み戻し廃止 (RV095 P3-1 / 要確認事項)
# ---------------------------------------------------------------------------
TEST_ENV=$(setup_mock_env "t37")
DRAFT_PATH="${TEST_ENV}/worktree/.input/review/reviewer-draft.md"

cat <<'EOF' > "${DRAFT_PATH}"
# Review Draft
- Role: reviewer
- Round: 1
## Summary
Draft body.
EOF

cd "${TEST_ENV}/worktree"
# check に --round は指定不可
CHECK_ROUND_ERR=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh check reviewer --status Approved --round 1 2>&1 || true)
if [[ "${CHECK_ROUND_ERR}" == *"--round オプションは使用できません"* ]]; then
  pass "Reject --round option for check subcommand (RV095 要確認事項)"
else
  fail "Reject --round option for check subcommand" "Output: ${CHECK_ROUND_ERR}"
fi

# state.json も --round もない場合、本文に Round: 1 があってもエラー停止すること (本文読み戻し廃止)
COMMENT_NO_ROUND_ERR=$(PATH="${TEST_ENV}/bin:${PATH}" ALLOW_REVIEW_SYNC_DEV=1 bash scripts/review-sync.sh comment reviewer --status Approved 2>&1 || true)
if [[ "${COMMENT_NO_ROUND_ERR}" == *"Round 番号を解決できませんでした"* ]]; then
  pass "Refuse fallback to draft body Round line and require state.json or --round (RV095 P3-1)"
else
  fail "Refuse fallback to draft body Round line and require state.json or --round" "Output: ${COMMENT_NO_ROUND_ERR}"
fi

echo "=========================================="
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [[ "${FAILED}" -gt 0 ]]; then
  exit 1
fi
exit 0
