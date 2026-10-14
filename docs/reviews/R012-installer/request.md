# レビュー依頼: R012: インストーラ & 環境セットアップ

- **タスクID**: `R012-installer`
- **GitHub Scope Issue**: [#27](https://github.com/okinawa-systembit/herdr-orchestration/issues/27)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **中**
- **依存タスク**: R011
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `install.sh`
- `skills/herdr-orchestrator/install/dev-local-install.sh`
- `skills/herdr-orchestrator/install/setup.sh`
- `skills/herdr-orchestrator/install/link-cli.sh`
- `skills/herdr-orchestrator/install/codex-execpolicy-snippet.rules`
- `skills/herdr-orchestrator/install/README.md`
- `scripts/verify-orchestrator.sh`
- `scripts/print-automated-verification-snippet.sh`

## 2. 参照ファイル

- `skills/herdr-orchestrator/references/distribution-install.md`
- `skills/herdr-orchestrator/references/dev-local-install.md`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/07-skill-and-implementation.md#sec-16`
- `docs/herdr-orchestrator/08-adoption-and-verification.md#sec-17`

## 4. 関連テスト

- `tests/test_distribution_installer.py`
- `tests/test_orchestrator_phase8_adoption.py`

## 5. 重点確認事項

- curl | bash での安全な実行（set -euo pipefail、パイプ途中終了対策）
- ~/.agents/skills/herdr-orchestration への安全な配置と既存保護
- PATH へのシンボリックリンク作成時の既存コマンド衝突検知
- アンインストール・ロールバック手順の完全性

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #27](https://github.com/okinawa-systembit/herdr-orchestration/issues/27) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
