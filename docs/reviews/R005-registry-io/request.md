# レビュー依頼: R005: Registry 永続化・排他制御・I/O

- **タスクID**: `R005-registry-io`
- **GitHub Scope Issue**: [#19](https://github.com/okinawa-systembit/herdr-orchestration/issues/19)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **中**
- **依存タスク**: R001, R004
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/registry/io.py`
- `skills/herdr-orchestrator/scripts/orchestrator/handoff_lock.py`

## 2. 参照ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`
- `skills/herdr-orchestrator/scripts/orchestrator/registry/task_id.py`
- `skills/herdr-orchestrator/scripts/orchestrator/paths.py`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/03-task-registry.md#sec-6-4`
- `docs/herdr-orchestrator/03-task-registry.md#sec-6-9`
- `docs/herdr-orchestrator/03-task-registry.md#sec-6-10`

## 4. 関連テスト

- `tests/test_orchestrator_phase2_registry.py`

## 5. 重点確認事項

- mutate_task における fcntl.flock(LOCK_EX) と os.replace の原子性
- 例外発生時の確実なロック解放（finally）と一時ファイルの残留防止
- パーミッション強制（ファイル: 0600, ディレクトリ: 0700）と symlink オープン拒否
- purge_expired_tasks による安全なタスク削除と handoff_lock の排他性

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #19](https://github.com/okinawa-systembit/herdr-orchestration/issues/19) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
