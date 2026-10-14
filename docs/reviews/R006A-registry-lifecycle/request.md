# レビュー依頼: R006A: Registry ライフサイクル & Dispatch 遷移

- **タスクID**: `R006A-registry-lifecycle`
- **GitHub Scope Issue**: [#20](https://github.com/okinawa-systembit/herdr-orchestration/issues/20)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **中**
- **依存タスク**: R004, R005
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py (L1–L247)`

## 2. 参照ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/registry/io.py`
- `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/03-task-registry.md#sec-6-3`
- `docs/herdr-orchestrator/03-task-registry.md#sec-6-6`
- `docs/herdr-orchestrator/03-task-registry.md#sec-6-12`

## 4. 関連テスト

- `tests/test_orchestrator_phase2_registry.py`
- `tests/test_orchestrator_phase5_dispatch.py`

## 5. 重点確認事項

- create_task 時の初期状態の厳密性
- record_prompt_start の重複実行防止（herdr_prompt_started_at の一意記録）
- mark_dispatch による 4 状態遷移と task/result/completion 連動
- find_stale_dispatch_candidates による Scenario A/B 判定ロジック

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #20](https://github.com/okinawa-systembit/herdr-orchestration/issues/20) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
