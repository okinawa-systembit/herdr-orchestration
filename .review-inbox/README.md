# `.review-inbox`（herdr-orchestration — 旧方式・読み取り専用履歴）

本ディレクトリは Phase 1 導入前の旧レビュー運用（単一固定ファイル上書き方式）の**読み取り専用履歴**です。  
新規のレビュー依頼・記録には使用しません。

現在のレビュー運用手順・正本成果物:

- 手順・Scope・CLI 規約: [repo-review SKILL.md](../.agents/skills/repo-review/SKILL.md)
- 新規レビュー依頼: `.inbox/review-request.<token>.md`（`scripts/repo-review-helper request-create` で作成）
- 新規レビュー記録: `docs/reviews/internal/<instance-id>/RVNNN.md`（`scripts/repo-review-helper record-save` で保存）

---

## 旧方式ファイル一覧（参考・過去履歴用）

| ファイル             | 用途（旧方式）                               |
| -------------------- | -------------------------------------------- |
| `review-request.md`  | requester → reviewer 依頼（旧方式）          |
| `review-response.md` | reviewer → requester 指摘（旧方式・上書き）   |
| `INDEX.md`           | ラウンド履歴（旧方式）                       |

※ `*.example.md` は旧方式のテンプレート例です（新規レビューでは使用しません）。
