# レビュー依頼: R004: Registry スキーマ・ID・安全パス

- **タスクID**: `R004-registry-schema`
- **対象コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d`
- **推定負荷**: **中**
- **依存タスク**: R001
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/registry/schema.py`
- `skills/herdr-orchestrator/scripts/orchestrator/registry/task_id.py`
- `skills/herdr-orchestrator/scripts/orchestrator/registry/paths_safe.py`
- `skills/herdr-orchestrator/scripts/orchestrator/registry/errors.py`
- `skills/herdr-orchestrator/scripts/orchestrator/registry/__init__.py`

## 2. 参照ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/paths.py`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-5`
- `docs/herdr-orchestrator/03-task-registry.md#sec-6-1`
- `docs/herdr-orchestrator/03-task-registry.md#sec-6-8`

## 4. 関連テスト

- `tests/test_orchestrator_phase2_registry.py`

## 5. 重点確認事項

- canonical schema v1 の必須フィールド網羅性と未知フィールドの排除
- 4軸ステータス（task, dispatch, result, completion）の許容値定義の完全性
- task_id 形式（task_ + UUID v4）の正規表現検証
- require_result_artifact におけるトラバーサル・symlink 攻撃の防御

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果は同ディレクトリの `result.md` に記録する。
   - レビュー台帳 [../INDEX.md](../INDEX.md) を更新する。
