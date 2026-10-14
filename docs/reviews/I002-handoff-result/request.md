# レビュー依頼: I002: Handoff 配送と Result 登録の契約結合レビュー

- **タスクID**: `I002-handoff-result`
- **対象コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d`
- **推定負荷**: **中**
- **依存タスク**: R003, R006B, R008, R009
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `R003 (Live Agent)`
- `R006B (Registry Ops)`
- `R008 (Handoff)`
- `R009 (Result)`

## 2. 参照ファイル

- なし

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/06-results-and-workflows.md#sec-10`
- `docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4`

## 4. 関連テスト

- `tests/test_orchestrator_phase3_result.py`
- `tests/test_orchestrator_phase4_handoff.py`

## 5. 重点確認事項

- worker 側での Task Envelope 受信から task-status 正本確認の契約遵守
- 早着 result (dispatch=pending) 時の retryable 拒否と dispatch 確定後の再登録
- worker の caller pane / worktree / repo 照合による不正登録遮断

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
