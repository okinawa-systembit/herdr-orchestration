# レビュー依頼: I003: Result 登録と Completion 通知・復旧の連携結合レビュー

- **タスクID**: `I003-result-completion`
- **対象コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d`
- **推定負荷**: **中**
- **依存タスク**: R006B, R009, R010
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `R006B (Registry Ops)`
- `R009 (Result)`
- `R010 (Completion)`

## 2. 参照ファイル

- なし

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/05-completion-and-recovery.md#sec-9`

## 4. 関連テスト

- `tests/test_orchestrator_phase3_result.py`
- `tests/test_orchestrator_phase6_completion.py`

## 5. 重点確認事項

- result_submit 直後の best-effort completion 連鎖と eligibility の厳密性
- 初回配送失敗タスクに対する completion claim および reset の完全拒絶
- recover-completion による reset と claim の 2 段階分離動作

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
