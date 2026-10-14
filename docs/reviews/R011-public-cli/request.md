# レビュー依頼: R011: Public CLI & レガシー CLI

- **タスクID**: `R011-public-cli`
- **GitHub Scope Issue**: [#26](https://github.com/okinawa-systembit/herdr-orchestration/issues/26)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **中**
- **依存タスク**: R001〜R010
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/scripts/herdr-orchestrator`
- `skills/herdr-orchestrator/scripts/orchestrator/public_cli.py`
- `skills/herdr-orchestrator/scripts/orchestrator/cli_output.py`
- `skills/herdr-orchestrator/scripts/task-registry.py`

## 2. 参照ファイル

- なし

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14`

## 4. 関連テスト

- `tests/test_orchestrator_phase0.py`
- `tests/test_orchestrator_phase2_registry.py`

## 5. 重点確認事項

- CLI 引数構文解析と --help の完全性
- 正常時 exit 0 + stdout 単一 JSON、異常時 exit 1 + error JSON の契約遵守
- 各種例外（Handoff, Result, Completion, Registry, HerdrEnv）の一貫したエラー出力
- task-registry.py との互換性維持

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #26](https://github.com/okinawa-systembit/herdr-orchestration/issues/26) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
