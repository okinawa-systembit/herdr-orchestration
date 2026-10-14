# レビュー依頼: I005: インストーラ・CI/CD・Skills のエコシステム整合性結合レビュー

- **タスクID**: `I005-ecosystem`
- **対象コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d`
- **推定負荷**: **中**
- **依存タスク**: R012, R013, R014
- **レビュー基準**: [.agents/skills/repo-review/SKILL.md](../../../.agents/skills/repo-review/SKILL.md) / [references.md](../../../.agents/skills/repo-review/references.md) / [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

---

## 1. 主レビュー対象ファイル

- `R012 (Installer)`
- `R013 (Skills)`
- `R014 (CI/CD)`

## 2. 参照ファイル

- なし

## 3. 参照設計書・仕様

- `docs/herdr-orchestrator/08-adoption-and-verification.md#sec-17`
- `docs/herdr-orchestrator/08-adoption-and-verification.md#sec-18`

## 4. 関連テスト

- `tests/test_distribution_installer.py`
- `tests/test_orchestrator_phase8_adoption.py`

## 5. 重点確認事項

- install.sh での配置と repo-review / repo-workflow からの呼び出し可能性
- CI パイプラインでの自動テストとインストーラ検証の整合性
- 設計書・ガイドライン・リファレンス間の記述完全一致

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
