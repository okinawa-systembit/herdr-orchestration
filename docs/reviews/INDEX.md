# herdr-orchestration 初回20 Scope レビュー定義一覧 (Review Scopes Index)

本書は、`herdr-orchestration` リポジトリにおける初回 20 Scope レビューの定義および GitHub Scope Issue への対応一覧文書である。

> [!IMPORTANT]
> **レビュー運用の正本について**:
>
> - レビューの進捗・判定・結果・指摘事項の追跡は、**GitHub Issues（Scope Issue #15〜#34 および Finding Issue）を正本**として一元管理します。
> - 本書は静的な定義・対応一覧であり、ローカルファイル（`INDEX.md` や `result.md`）を動的な進捗台帳として更新する運用は行いません。
> - 運用の詳細設計は [CONTINUOUS_REVIEW_SIMPLIFIED_DESIGN.md](CONTINUOUS_REVIEW_SIMPLIFIED_DESIGN.md)、レビュー全体の計画は [PLAN.md](PLAN.md) を参照してください。

---

## 1. 初回レビュー概要

- **対象リポジトリ**: `okinawa-systembit/herdr-orchestration`
- **対象ブランチ**: `main`
- **初期基準コミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d` (v0.1.2)
- **共通対象コミットSHA**: 初回全体レビュー開始時に確定した共通対象コミットSHA（全20 Scope原則同一。レビュー途中でmainが更新されても自動変更しない）
- **Scope 総数**: 20 Scope（個別レビュー: 15 / 結合レビュー: 5）
- **レビュー基準**:
  - [.agents/skills/repo-review/SKILL.md](../../.agents/skills/repo-review/SKILL.md)
  - [.agents/skills/repo-review/references.md](../../.agents/skills/repo-review/references.md)
  - [docs/herdr-agent-review-guidelines.md](../../docs/herdr-agent-review-guidelines.md)
- **進捗・結果の確認先**: [GitHub Scope Issue 一覧 (label: scope-review)](https://github.com/okinawa-systembit/herdr-orchestration/labels/scope-review)

---

## 2. 個別レビュー Scope 一覧 (Unit Review Scopes)

| Scope ID  | 機能・責務                               | 主対象ファイル                                                             |  負荷  | 依存 Scope                    | GitHub Scope Issue                                                        | レビュー依頼書                                                             |
| --------- | ---------------------------------------- | -------------------------------------------------------------------------- | :----: | ----------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **R001**  | 基盤・パス・環境・エラー・命名規則       | `paths.py`, `env.py`, `errors.py`, `worktree.py`, `naming.py`              | **小** | なし                          | [#15](https://github.com/okinawa-systembit/herdr-orchestration/issues/15) | [R001-primitives/request.md](R001-primitives/request.md)                   |
| **R002**  | リポジトリ識別 & Herdr CLI 基盤          | `identity.py`, `herdr_cli.py`                                              | **中** | R001                          | [#16](https://github.com/okinawa-systembit/herdr-orchestration/issues/16) | [R002-identity-cli/request.md](R002-identity-cli/request.md)               |
| **R003**  | Live Agent 検証 & Worker 追跡            | `live_agent.py`, `worker_trace.py`                                         | **中** | R001, R002                    | [#17](https://github.com/okinawa-systembit/herdr-orchestration/issues/17) | [R003-live-agent/request.md](R003-live-agent/request.md)                   |
| **R004**  | Registry スキーマ・ID・安全パス          | `schema.py`, `task_id.py`, `paths_safe.py`, `errors.py`                    | **中** | R001                          | [#18](https://github.com/okinawa-systembit/herdr-orchestration/issues/18) | [R004-registry-schema/request.md](R004-registry-schema/request.md)         |
| **R005**  | Registry 永続化・排他制御・I/O           | `registry/io.py`, `handoff_lock.py`                                        | **中** | R001, R004                    | [#19](https://github.com/okinawa-systembit/herdr-orchestration/issues/19) | [R005-registry-io/request.md](R005-registry-io/request.md)                 |
| **R006A** | Registry タスクライフサイクル & Dispatch | `registry/operations.py` (L1–L247)                                         | **中** | R004, R005                    | [#20](https://github.com/okinawa-systembit/herdr-orchestration/issues/20) | [R006A-registry-lifecycle/request.md](R006A-registry-lifecycle/request.md) |
| **R006B** | Registry Result & Completion 遷移・Claim | `registry/operations.py` (L248–L551)                                       | **中** | R004, R005, R006A             | [#21](https://github.com/okinawa-systembit/herdr-orchestration/issues/21) | [R006B-registry-ops/request.md](R006B-registry-ops/request.md)             |
| **R007**  | Dispatch コマンド & 診断・復旧           | `dispatch_commands.py`                                                     | **小** | R003, R005, R006A             | [#22](https://github.com/okinawa-systembit/herdr-orchestration/issues/22) | [R007-dispatch/request.md](R007-dispatch/request.md)                       |
| **R008**  | Handoff オーケストレーション & Sync      | `handoff_commands.py`                                                      | **中** | R002, R003, R005, R006A       | [#23](https://github.com/okinawa-systembit/herdr-orchestration/issues/23) | [R008-handoff/request.md](R008-handoff/request.md)                         |
| **R009**  | Result 登録・読み取りコマンド            | `result_commands.py`                                                       | **中** | R003, R004, R006B             | [#24](https://github.com/okinawa-systembit/herdr-orchestration/issues/24) | [R009-result/request.md](R009-result/request.md)                           |
| **R010**  | Async Completion & リカバリー            | `completion_commands.py`                                                   | **中** | R002, R003, R006B, R008, R009 | [#25](https://github.com/okinawa-systembit/herdr-orchestration/issues/25) | [R010-completion/request.md](R010-completion/request.md)                   |
| **R011**  | Public CLI & レガシー CLI                | `herdr-orchestrator`, `public_cli.py`, `cli_output.py`, `task-registry.py` | **中** | R001〜R010                    | [#26](https://github.com/okinawa-systembit/herdr-orchestration/issues/26) | [R011-public-cli/request.md](R011-public-cli/request.md)                   |
| **R012**  | インストーラ & 環境セットアップ          | `install.sh`, `skills/.../install/*.sh`, `scripts/*.sh`                    | **中** | R011                          | [#27](https://github.com/okinawa-systembit/herdr-orchestration/issues/27) | [R012-installer/request.md](R012-installer/request.md)                     |
| **R013**  | Skills 定義 & 運用リファレンス           | `skills/.../SKILL.md`, `references/*.md`, `.agents/skills/*`               | **中** | R011, R012                    | [#28](https://github.com/okinawa-systembit/herdr-orchestration/issues/28) | [R013-skills/request.md](R013-skills/request.md)                           |
| **R014**  | CI/CD パイプライン & リリース            | `.github/workflows/*.yml`, `package.json`                                  | **小** | R012, R013                    | [#29](https://github.com/okinawa-systembit/herdr-orchestration/issues/29) | [R014-cicd/request.md](R014-cicd/request.md)                               |

---

## 3. 結合レビュー Scope 一覧 (Integration Review Scopes)

| Scope ID | 結合検証テーマ                                   | 関連個別タスク               |  負荷  | GitHub Scope Issue                                                        | レビュー依頼書                                                         |
| -------- | ------------------------------------------------ | ---------------------------- | :----: | ------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **I001** | Registry と Handoff / Dispatch の状態整合性      | R005, R006A, R007, R008      | **中** | [#30](https://github.com/okinawa-systembit/herdr-orchestration/issues/30) | [I001-registry-handoff/request.md](I001-registry-handoff/request.md)   |
| **I002** | Handoff 配送と Result 登録の契約                 | R003, R006B, R008, R009      | **中** | [#31](https://github.com/okinawa-systembit/herdr-orchestration/issues/31) | [I002-handoff-result/request.md](I002-handoff-result/request.md)       |
| **I003** | Result 登録と Completion 通知・復旧の連携        | R006B, R009, R010            | **中** | [#32](https://github.com/okinawa-systembit/herdr-orchestration/issues/32) | [I003-result-completion/request.md](I003-result-completion/request.md) |
| **I004** | CLI と内部コマンドのエラー契約・終了コード       | R007, R008, R009, R010, R011 | **中** | [#33](https://github.com/okinawa-systembit/herdr-orchestration/issues/33) | [I004-cli-contract/request.md](I004-cli-contract/request.md)           |
| **I005** | インストーラ・CI/CD・Skills のエコシステム整合性 | R012, R013, R014             | **中** | [#34](https://github.com/okinawa-systembit/herdr-orchestration/issues/34) | [I005-ecosystem/request.md](I005-ecosystem/request.md)                 |

---

## 4. 指摘管理方針 (Finding Management)

レビューによって検出された指摘事項（Finding）は、GitHub Finding Issue として起票・管理します。

- **起票単位**: 原則として 1 指摘 = 1 Issue
- **ラベル体系**（`scripts/setup-review-labels.sh` に定義）:
  - 種別: `review-finding`
  - 重大度: `severity:p0` (致命的), `severity:p1` (重大・Blocking), `severity:p2` (通常・Non-blocking), `severity:p3` (軽微・改善)
  - 進行ステータス: `status:ready-to-fix` (修正待ち), `status:verified` (修正確認完了) ※レビュー中は `status:in-review`
- **Scope Issue との紐付け**: Finding Issue の本文に対象 Scope Issue へのリンク（例: `Scope: #15`）を含め、Scope Issue 側からも検出指摘一覧として相互リンクします。

---

## 5. レビュー実施の流れ

1. **着手**:
   - 初回全体レビューの共通対象コミットSHA（全 20 Scope 原則同一。レビュー期間中に `main` が更新されても自動追従せず固定検証）を確認します。
   - レビュー観点・主対象ファイル・テスト仕様は各 Scope の `request.md` を参照します。
2. **レビュー実行**:
   - `repo-review` Skill の 2 段階レビュー（仕様整合性検証・批判的不具合探索）および反証確認を実施します。
3. **結果記録**:
   - レビュー結果（総合判定、確認コミットSHA、検出指摘、未実施検証）を GitHub Scope Issue のコメントに投稿します。
   - 未解決指摘がある場合は GitHub Finding Issue を起票し、Scope Issue と相互リンクします。
4. **レビュー作業完了 (Scope Issue のクローズ)**:
   - レビュー結果の記録と、未解決指摘がある場合の Finding Issue 起票・相互リンクが完了したら、レビュー作業完了として Scope Issue をクローズします（`gh issue close <number> --reason "completed"`）。
   - **重要**: Scope Issue のクローズはレビュー作業（解析・判定・結果記録・指摘起票）の完了を示すものであり、未解決 Finding が存在しないことの証明ではありません。指摘事項の修正・解決状況は各 Finding Issue 側で独立して追跡・クローズします。

---

## 6. 全体完了判定 (Global GO Gate)

リポジトリ全体の初回レビュー完了（Global GO）は、単一 Scope の完了ではなく、以下の条件をすべて満たした場合に下されます：

- [ ] 全 20 の Scope Issue（R001〜R014, I001〜I005）がレビュー完了として Closed（総合判定 `GO`）であること。
- [ ] 検出されたすべての Blocking 指摘（`severity:p0`, `severity:p1` の Finding Issue）が是正され、Closed（`status:verified`）であること。
- [ ] Non-blocking 指摘（`severity:p2`, `severity:p3`）について、保留または採否が明確に合意されていること。
- [ ] すべての Scope が同一の共通確定コミットSHA（および是正コミット）に対して検証されていること。
