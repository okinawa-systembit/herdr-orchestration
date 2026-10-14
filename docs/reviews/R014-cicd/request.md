# レビュー依頼: R014: CI/CD パイプライン & リリース

- **タスクID**: `R014-cicd`
- **GitHub Scope Issue**: [#29](https://github.com/okinawa-systembit/herdr-orchestration/issues/29)
- **基準コミットSHA (初期基準点)**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **対象コミットSHA**: レビュー着手時に最新の対象コミット（main HEAD等）を確定・記録する
- **推定負荷**: **小**
- **依存タスク**: R012, R013
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `package.json`
- `package-lock.json`

## 2. 参照ファイル

- `install.sh`
- `scripts/verify-orchestrator.sh`

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/08-adoption-and-verification.md#sec-18`
- `docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20`

## 4. 関連テスト

- `GitHub Actions 構文検証, npm scripts`

## 5. 重点確認事項

- CI checkout 安全性、concurrency 制御による競合防止
- 自動テストゲート（format, lint, unittest 199件, verify）の完全性
- リリースワークフローにおけるタグトリガー、アセットパッケージング、権限最小化
- package.json の scripts 定義と実ファイルパスの一致

---

## 6. レビュー手順・必須条件

1. **第1段階：仕様・設計との整合性検証**:
   - 要求仕様や設計書の契約・定義と整合しているか確認する。
2. **第2段階：批判的な不具合探索（必須）**:
   - 設計書と一致したことだけを理由に GO とせず、境界値・例外経路・競合・既存保証の喪失を探索する。
3. **反証確認（必須）**:
   - 指摘候補について「既存処理で防がれていないか」「具体的な失敗条件が成立するか」を確認する。
4. **結果の記録**:
   - 初回レビュー結果（判定、指摘事項、未実施検証）は対応する [GitHub Scope Issue #29](https://github.com/okinawa-systembit/herdr-orchestration/issues/29) のコメントに記録する。
   - 未解決指摘（P0〜P3）が検出された場合は、GitHub Finding Issue を起票し、Scope Issue と相互リンクする。
   - ローカルの `result.md` や動的台帳への記録・更新は行わない（GitHub が正本）。
