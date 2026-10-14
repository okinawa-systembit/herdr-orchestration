# レビュー依頼: R010: Async Completion & リカバリー

- **タスクID**: `R010-completion`
- **対象コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d`
- **推定負荷**: **中**
- **依存タスク**: R002, R003, R006B, R008, R009
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/completion_commands.py`

## 2. 参照ファイル

- `skills/herdr-orchestrator/scripts/orchestrator/registry/operations.py`
- `skills/herdr-orchestrator/scripts/orchestrator/live_agent.py`
- `skills/herdr-orchestrator/scripts/orchestrator/herdr_cli.py`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9`

## 4. 関連テスト

- `tests/test_orchestrator_phase6_completion.py`

## 5. 重点確認事項

- pane_id 不在時や各種失敗時に finish_completion を確実に呼び processing 放置を防ぐこと
- 2 回目 resolve_requester() 後の agent_status 再判定（TOCTOU 抑止）
- run_recover_completion における sent/uncertain/processing に対する二重配送抑止
- reset-completion と recover-completion の 2 段階分離設計

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
