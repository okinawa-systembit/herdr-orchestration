# herdr-orchestration 全体レビュー台帳 (Review Ledger)

本台帳は、`herdr-orchestration` リポジトリ全体の分割コードレビューの進捗、判定、指摘事項の追跡を一元管理する正本文書である。
計画の詳細は [PLAN.md](PLAN.md) を参照すること。

---

## 1. レビュー実行サマリー

- **対象リポジトリ**: `okinawa-systembit/herdr-orchestration`
- **対象ブランチ**: `main`
- **ベースコミットSHA**: `e527edc67866ffee5d7ec1527e1274b4ab395a9d`
- **全体ステータス**: **計画完了・レビュー未着手 (READY)**
- **全体判定**: **未判定 (PENDING)**
- **タスク総数**: 20 タスク（個別レビュー: 15 / 結合レビュー: 5）
- **完了タスク数**: 0 / 20
- **総 Blocking 指摘数**: 0
- **総 未解決指摘数**: 0

---

## 2. 個別レビュー台帳 (Unit Reviews)

| ID        | 機能・責務                               | 主対象ファイル                                                             | 対象SHA   | 進捗   | 最新判定 | Blocking | 未解決 | 最終実施日 | 使用モデル | 最新結果リンク                                                             |
| --------- | ---------------------------------------- | -------------------------------------------------------------------------- | --------- | ------ | -------- | -------: | -----: | ---------- | ---------- | -------------------------------------------------------------------------- |
| **R001**  | 基盤・パス・環境・エラー・命名規則       | `paths.py`, `env.py`, `errors.py`, `worktree.py`, `naming.py`              | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R001-primitives/request.md](R001-primitives/request.md)                   |
| **R002**  | リポジトリ識別 & Herdr CLI 基盤          | `identity.py`, `herdr_cli.py`                                              | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R002-identity-cli/request.md](R002-identity-cli/request.md)               |
| **R003**  | Live Agent 検証 & Worker 追跡            | `live_agent.py`, `worker_trace.py`                                         | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R003-live-agent/request.md](R003-live-agent/request.md)                   |
| **R004**  | Registry スキーマ・ID・安全パス          | `schema.py`, `task_id.py`, `paths_safe.py`, `errors.py`                    | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R004-registry-schema/request.md](R004-registry-schema/request.md)         |
| **R005**  | Registry 永続化・排他制御・I/O           | `registry/io.py`, `handoff_lock.py`                                        | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R005-registry-io/request.md](R005-registry-io/request.md)                 |
| **R006A** | Registry タスクライフサイクル & Dispatch | `registry/operations.py` (L1–L247)                                         | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R006A-registry-lifecycle/request.md](R006A-registry-lifecycle/request.md) |
| **R006B** | Registry Result & Completion 遷移・Claim | `registry/operations.py` (L248–L551)                                       | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R006B-registry-ops/request.md](R006B-registry-ops/request.md)             |
| **R007**  | Dispatch コマンド & 診断・復旧           | `dispatch_commands.py`                                                     | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R007-dispatch/request.md](R007-dispatch/request.md)                       |
| **R008**  | Handoff オーケストレーション & Sync      | `handoff_commands.py`                                                      | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R008-handoff/request.md](R008-handoff/request.md)                         |
| **R009**  | Result 登録・読み取りコマンド            | `result_commands.py`                                                       | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R009-result/request.md](R009-result/request.md)                           |
| **R010**  | Async Completion & リカバリー            | `completion_commands.py`                                                   | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R010-completion/request.md](R010-completion/request.md)                   |
| **R011**  | Public CLI & レガシー CLI                | `herdr-orchestrator`, `public_cli.py`, `cli_output.py`, `task-registry.py` | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R011-public-cli/request.md](R011-public-cli/request.md)                   |
| **R012**  | インストーラ & 環境セットアップ          | `install.sh`, `skills/.../install/*.sh`, `scripts/*.sh`                    | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R012-installer/request.md](R012-installer/request.md)                     |
| **R013**  | Skills 定義 & 運用リファレンス           | `skills/.../SKILL.md`, `references/*.md`, `.agents/skills/*`               | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R013-skills/request.md](R013-skills/request.md)                           |
| **R014**  | CI/CD パイプライン & リリース            | `.github/workflows/*.yml`, `package.json`                                  | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [R014-cicd/request.md](R014-cicd/request.md)                               |

---

## 3. 結合レビュー台帳 (Integration Reviews)

| ID       | 結合検証テーマ                                   | 関連個別タスク               | 対象SHA   | 進捗   | 最新判定 | Blocking | 未解決 | 最終実施日 | 使用モデル | 最新結果リンク                                                         |
| -------- | ------------------------------------------------ | ---------------------------- | --------- | ------ | -------- | -------: | -----: | ---------- | ---------- | ---------------------------------------------------------------------- |
| **I001** | Registry と Handoff / Dispatch の状態整合性      | R005, R006A, R007, R008      | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [I001-registry-handoff/request.md](I001-registry-handoff/request.md)   |
| **I002** | Handoff 配送と Result 登録の契約                 | R003, R006B, R008, R009      | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [I002-handoff-result/request.md](I002-handoff-result/request.md)       |
| **I003** | Result 登録と Completion 通知・復旧の連携        | R006B, R009, R010            | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [I003-result-completion/request.md](I003-result-completion/request.md) |
| **I004** | CLI と内部コマンドのエラー契約・終了コード       | R007, R008, R009, R010, R011 | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [I004-cli-contract/request.md](I004-cli-contract/request.md)           |
| **I005** | インストーラ・CI/CD・Skills のエコシステム整合性 | R012, R013, R014             | `e527edc` | 未着手 | 未判定   |        0 |      0 | -          | -          | [I005-ecosystem/request.md](I005-ecosystem/request.md)                 |

---

## 4. 全体指摘追跡一覧 (Master Findings Tracking)

各タスクで検出された指摘事項を横断して記録する。
指摘ID体系: `{TaskID}-F{連番3桁}`（例: `R001-F001`, `R010-F001`, `I001-F001`）

| 指摘ID | 発見タスク | 重大度 | 分類 | 状態 | 概要                   | 検出コミット | 是正コミット | 備考 |
| ------ | ---------- | ------ | ---- | ---- | ---------------------- | ------------ | ------------ | ---- |
| -      | -          | -      | -    | -    | (レビュー実施時に追記) | -            | -            | -    |

- **状態定義**:
  - `未対応`: 指摘が受理され、修正が未着手
  - `対応済み`: 修正コードが検証され、問題が解消したことを確認
  - `部分対応`: 一部のみ解消、未解決部分が残存
  - `誤検知/不採用`: 反証確認または仕様確認により不具合ではないと合意
  - `保留`: 影響度とリスクを評価の上、次期リリースへ延期合意

---

## 5. 運用ルール・更新手順

1. **レビュー着手時**:
   - `進捗` を `実施中` に更新し、`使用モデル` と開始日を記録する。
2. **レビュー完了時**:
   - `進捗` を `完了` に更新する。
   - `最新判定`（`GO` または `NO-GO` 等）、`Blocking` 指摘数、`未解決` 指摘数を記録する。
   - `docs/reviews/{TaskID}/result.md`（再レビュー時は `recheck-01.md`）へのリンクを記載する。
   - 新規指摘があれば「4. 全体指摘追跡一覧」に行を追加する。
3. **レビュー中断時**:
   - 利用枠超過やエラー等で中断した場合は、`進捗` を `中断` に更新する。
   - **中断タスクを GO と判定してはならない**。`最新判定` は `未判定` または `検証不能（中断）` とする。
   - 当該タスクの `result.md` に中断理由、確認済み範囲、未確認範囲を明記する。
4. **全体 GO 判定基準**:
   - 全 20 タスク（R001〜R014, I001〜I005）の進捗が `完了` であること。
   - 全タスクの最新判定が `GO` であること。
   - 全指摘追跡において `Blocking` 指摘が 0 件、かつ未解決指摘の扱いが合意されていること。
   - 全タスクのレビュー対象コミットSHAが同一（または差分追従済み）であること。
