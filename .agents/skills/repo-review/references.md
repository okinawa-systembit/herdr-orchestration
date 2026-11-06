# herdr-orchestration レビュー — 参照（目次・共通前提）

<a id="roles-and-panes"></a>

## 1. 役割と pane

本リポジトリでは [repo-workflow](../repo-workflow/SKILL.md) と共通の日本語ロール名（`レビュワー`、`メイン`、`設計担当`、`実装担当`）を使用する。

| 論理 role               | 例（この workspace）       | 備考                                                    |
| :---------------------- | :------------------------- | :------------------------------------------------------ |
| requester / implementer | `${HERDR_WORKSPACE_ID}:p1` | handoff 実行 pane（`メイン` / `設計担当` / `実装担当`） |
| reviewer worker         | `${HERDR_WORKSPACE_ID}:p2` | `--to レビュワー` の解決先                              |

- `herdr agent list`（および `herdr-orchestrator role show`）で pane と role の紐づけを確認。Registry の `worker.agent_name`（Role Binding 利用時は `pane_id` が格納済み）と reviewer 自身の `HERDR_PANE_ID`（`pane_id`）または live `agent.name` が一致すること（[設計 §11.4](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)）。
- **GitHub 操作権限とコメント投稿条件（正本: [repo-workflow §7](../repo-workflow/SKILL.md#github-write-matrix)）**:
  - **コメント投稿条件**: 内部レビューはローカル完結を基本とし、依頼元から明示的に指示された場合（request や instruction での指定）に限り、対象 Finding Issue へレビュー結果をコメント投稿（`gh issue comment`）する。投稿前に Issue 番号・リポジトリ・対象 SHA を確認し、レビューGO を「解決完了」と表現しない。API エラー時はレビュー自体を失敗とせず警告として扱う。
  - **クローズ・変更権限**: Finding Issue を含む GitHub Issue のクローズ（`gh issue close`）、再オープン（`gh issue reopen`）、およびラベル変更（`gh issue edit`）は **人間のみが実施する**（エージェントによる実行は厳禁。依頼元エージェントは PR マージ後の再検証と解決確認コメントの記録・人間へのクローズ推奨報告までを担当する）。

---

<a id="progressive-disclosure-index"></a>

## 2. 段階的開示（Progressive Disclosure）リファレンス目次

用途・役割に応じて以下の各リファレンスを参照する：

1. **判定基準・品質基準の正本**: **[references/criteria.md](references/criteria.md)**
   - 指摘の 3 分類、重大度（P0〜P3）と Blocking 判定規則（[#review-principles](references/criteria.md#review-principles)）
   - 8 つの反証確認問い（[#refutation-checks](references/criteria.md#refutation-checks)）
   - GO 判定条件・P3 再レビュー不要ルール（[#go-conditions](references/criteria.md#go-conditions)）
   - 再レビュー時の検証方針・反証成否記録表（[#re-review-rules](references/criteria.md#re-review-rules)）
   - 実行結果と推論の区別（[#fact-vs-inference](references/criteria.md#fact-vs-inference)）
2. **出力テンプレートの正本**: **[references/template.md](references/template.md)**
   - レビュー記録（RVNNN.md）テンプレート（P3 1 行形式を含む）（[#review-response-body](references/template.md#review-response-body)）
   - レビュー判定と GitHub コメントメタデータ対応表（[#review-status-mapping](references/template.md#review-status-mapping)）
   - 人間報告フォーマット（[#human-report](references/template.md#human-report)）
3. **チェックリスト群**: **[references/checklists.md](references/checklists.md)**
   - レビュー Scope チェックリスト（[#review-scope](references/checklists.md#review-scope)）
   - 基本方針（原則 1〜7・指針 8〜10）チェックリスト
   - テスト実効性・汎用設計・セキュリティチェックリスト
   - レビュー Tier（LIGHT / STANDARD / DEEP）と差分外波及確認（D-1〜D-3）（[#review-tiers](references/checklists.md#review-tiers)）
   - ワイドレビュー（総点検）A-1〜A-8（[#wide-review](references/checklists.md#wide-review)）
4. **依頼側（Requester）手順**: **[references/requester.md](references/requester.md)**
   - ローカルコミット作成（C-15）、request-create、handoff 例、結果確認（[#requester-procedure](references/requester.md#requester-procedure)）
5. **レビューイ（Reviewee）対応手順**: **[references/reviewee.md](references/reviewee.md)**
   - 即時修正禁止、事前トリアージ、Decision 3 態、回答ドラフトテンプレート（[#reviewee-response-workflow](references/reviewee.md#reviewee-response-workflow)）
6. **GitHub PR 連携手順**: **[references/github-pr.md](references/github-pr.md)**
   - PR レビュー手順（--pr 明示指定による main 維持）、安全制約（Approve/Merge 禁止）（[#github-pr-review](references/github-pr.md#github-pr-review)）

---

## 3. 旧アンカー互換エイリアス

外部（`review-finding`、`review-skill-audit`、設計書等）からの既存参照を維持するためのアンカー：

- <a id="review-principles"></a> → **[references/criteria.md#review-principles](references/criteria.md#review-principles)**
- <a id="refutation-checks"></a> → **[references/criteria.md#refutation-checks](references/criteria.md#refutation-checks)**
- <a id="review-response-body"></a> → **[references/template.md#review-response-body](references/template.md#review-response-body)**
- <a id="review-status-mapping"></a> → **[references/template.md#review-status-mapping](references/template.md#review-status-mapping)**
- <a id="review-scope"></a> → **[references/checklists.md#review-scope](references/checklists.md#review-scope)**
- <a id="review-tiers"></a> → **[references/checklists.md#review-tiers](references/checklists.md#review-tiers)**
- <a id="wide-review"></a> → **[references/checklists.md#wide-review](references/checklists.md#wide-review)**
- <a id="reviewee-response-workflow"></a> → **[references/reviewee.md#reviewee-response-workflow](references/reviewee.md#reviewee-response-workflow)**
- <a id="human-report"></a> → **[references/template.md#human-report](references/template.md#human-report)**

---

## 4. Herdr prompt（補助・1 行のみ）

sync handoff が prompt 配送するのが正。足りない場合のみ:

```bash
herdr agent prompt "${HERDR_WORKSPACE_ID}:p2" '.input/inbox/<request-file> を読め'
```

```bash
herdr agent prompt "${HERDR_WORKSPACE_ID}:p1" 'docs/reviews/internal/<instance-id>/RVNNN.md を読め'
```

長文を prompt に載せない。
