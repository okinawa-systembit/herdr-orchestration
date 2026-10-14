# レビュー依頼: I002: Handoff 配送と Result 登録の契約結合レビュー

- **タスクID**: `I002-handoff-result`
- **GitHub Scope Issue**: [#31](https://github.com/okinawa-systembit/herdr-orchestration/issues/31)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
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
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #31](https://github.com/okinawa-systembit/herdr-orchestration/issues/31) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
