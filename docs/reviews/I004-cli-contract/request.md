# レビュー依頼: I004: CLI と内部コマンドのエラー契約・終了コード結合レビュー

- **タスクID**: `I004-cli-contract`
- **GitHub Scope Issue**: [#33](https://github.com/okinawa-systembit/herdr-orchestration/issues/33)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **中**
- **依存タスク**: R007, R008, R009, R010, R011
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `R007 (Dispatch)`
- `R008 (Handoff)`
- `R009 (Result)`
- `R010 (Completion)`
- `R011 (Public CLI)`

## 2. 参照ファイル

- なし

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14`

## 4. 関連テスト

- `tests/test_orchestrator_phase0.py`
- `tests/test_orchestrator_phase2_registry.py`

## 5. 重点確認事項

- 全コマンドでの JSON 出力契約と終了コード（成功:0, 失敗:1）の完全性
- 標準出力への JSON 単一出力規約（生テキスト混入の排除）
- 参照系 CLI（task-status, task-result, doctor）の契約一貫性

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #33](https://github.com/okinawa-systembit/herdr-orchestration/issues/33) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
