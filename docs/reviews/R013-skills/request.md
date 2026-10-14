# レビュー依頼: R013: Skills 定義 & 運用リファレンス

- **タスクID**: `R013-skills`
- **GitHub Scope Issue**: [#28](https://github.com/okinawa-systembit/herdr-orchestration/issues/28)
- **初期基準コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: 初回全体レビュー開始時に確定した共通対象コミットSHA（全Scope原則同一。レビュー途中でmainが更新されても自動変更しない）
- **推定負荷**: **中**
- **依存タスク**: R011, R012
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `skills/herdr-orchestrator/SKILL.md`
- `skills/herdr-orchestrator/references/*.md (8ファイル)`
- `.agents/skills/repo-review/SKILL.md`
- `.agents/skills/repo-review/references.md`
- `.agents/skills/repo-workflow/SKILL.md`
- `.agents/skills/design-doc-markdown-format/SKILL.md`

## 2. 参照ファイル

- なし

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4`
- `docs/herdr-orchestrator/06-results-and-workflows.md#sec-12`
- `docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14`
- `docs/herdr-orchestrator/08-adoption-and-verification.md#sec-19`

## 4. 関連テスト

- `tests/test_orchestrator_phase7_skill.py`

## 5. 重点確認事項

- YAML frontmatter (name, description) の構文妥当性
- コマンド例の実装との 100% 一致（オプション名、引数形式）
- Herdr 公式 Skill との責務分離（公式仕様を複製せず委譲）
- review-inbox 規約とラウンド履歴管理の整合性

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、対象コミットSHA、指摘事項、未実施検証）は対応する [GitHub Scope Issue #28](https://github.com/okinawa-systembit/herdr-orchestration/issues/28) のコメントに記録する。
   - 未解決指摘が検出された場合は、GitHub Finding Issue（ラベル: `review-finding`, `severity:p*`）を起票し、Scope Issue と相互リンクする。
   - レビュー結果の記録と必要な Finding Issue の起票・リンクが完了したら、レビュー作業完了として Scope Issue をクローズする（指摘事項の解決状況は Finding Issue 側で管理・追跡する）。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
