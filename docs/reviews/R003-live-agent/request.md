# レビュー依頼: R003: Live Agent 検証 & Worker 追跡

- **タスクID**: `R003-live-agent`
- **GitHub Scope Issue**: [#17](https://github.com/okinawa-systembit/herdr-orchestration/issues/17)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **中**
- **依存タスク**: R001, R002
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py`
- `skills/herdr-orchestrator/scripts/orchestrator/worker_trace.py`

## 2. 参照ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py`
- `skills/herdr-orchestrator/scripts/orchestrator/identity.py`
- `skills/herdr-orchestrator/scripts/orchestrator/worktree.py`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4`
- `docs/herdr-orchestrator/04-handoff-and-requester.md#sec-8`
- `docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4`

## 4. 関連テスト

- `tests/test_orchestrator_phase1_live_agent.py`

## 5. 重点確認事項

- get_live_agent における agent get ペイロードの厳格検証
- verify_worktree_affinity による実パス解決と識別子の一致確認
- assert_promptable_state による idle/done のみの許容と working/blocked/unknown 拒絶
- worker_trace_matches_task による多重起動防止の正確性

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #17](https://github.com/okinawa-systembit/herdr-orchestration/issues/17) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
