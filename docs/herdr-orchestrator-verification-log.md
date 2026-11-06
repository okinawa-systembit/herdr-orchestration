# Herdr Orchestrator — §18 検証記録（テンプレート）

> 正本チェックリスト: [§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3)  
> 実装 INDEX: [段階8](herdr-orchestrator-implementation-index.md#8-導入と総合確認)

運用者が実機確認後に追記する。項目の仕様本文は設計書を正本とし、ここには **結果だけ**を書く。

## 環境スナップショット

| 項目                            | 値                                                                              |
| ------------------------------- | ------------------------------------------------------------------------------- |
| 記録日                          | 2026-11-06                                                                      |
| 対象 repository                 | herdr-orchestration                                                             |
| Herdr CLI (`herdr --version`)   | herdr 0.9.5                                                                     |
| Herdr server / workspace        | w15（開発環境）                                                                 |
| 公式 herdr Skill 導入方法       | （各環境の §17.2 に従い記録）                                                   |
| herdr-orchestrator Skill 配置先 | リポジトリ同梱 + `install/setup.sh` または `npm run install:orchestrator-skill` |
| `npm run test:orchestrator`     | 423 passed（skipped=1, コミット a8c9f2d / 2026-11-06）                          |
| `npm run verify:orchestrator`   | OK（コミット a8c9f2d / 2026-11-06）                                             |

<a id="sec-agent-verification"></a>

## AI Agent 別の動作確認状況（実機検証単一正本）

各 AI Agent 環境における Herdr Orchestrator（Skill / CLI / Role Binding / Handoff）の実機検証状況および固有の制約事項を記録する。

| Agent                 | 動作状況                                 | 実機稼働実績・検証内容                                                                                                                                                              | 固有の制約・注意事項                                                                                                                                                                                                                                                                                                            |
| :-------------------- | :--------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Antigravity (Agy)** | 実機稼働確認済み（2026-11-06 確認）      | 本リポジトリの日常運用・各機能の設計/実装ロール（`w15:p1`, `w15:p4`, `w15:p5` 等）にて継続運用。Role Binding、sync/async handoff、task-result、result-submit を実機確認。           | なし                                                                                                                                                                                                                                                                                                                            |
| **Claude Code**       | 実機稼働確認済み（2026-11-06 確認）      | 本リポジトリの日常運用・コードレビュワーロール（`w15:p2` 等）にて継続運用。sync handoff によるレビュー依頼受信、タスク結果作成・提出（`record-save` / `result-submit`）を実機確認。 | なし                                                                                                                                                                                                                                                                                                                            |
| **Cursor**            | 未確認（過去に旧配置先での利用歴あり）   | 共通 Skill 配置先（`~/.agents/skills`）からの実機動作は現環境未確認（過去にレガシー配置先 `~/.cursor/skills` での利用歴あり）。                                                     | なし                                                                                                                                                                                                                                                                                                                            |
| **Codex**             | 未確認（過去に利用歴・制約判明実績あり） | 現環境での CLI 直接実行は未確認（`codex` コマンド未導入）。過去の利用歴および Issue #122 にて共有デーモンに起因する宛先不一致問題が判明済み。                                       | **既知の制約あり**。共有デーモン無効化（`daemon_auto_start = false`）が必要（詳細は [README.md FAQ](../README.md#q-codex-agent-でタスク受信時に宛先不一致caller_not_task_participantエラーで停止する) および [public-cli.md](../skills/herdr-orchestrator/references/public-cli.md#61-実行承認設定-codex-execpolicy) を参照）。 |

## 自動テスト対応（CLI / Registry）

`npm run test:orchestrator` がカバーする §18.3 項目は各 `tests/test_orchestrator_phase*.py` ファイル先頭コメントを参照。最新テスト実行結果・スナップショットは [環境スナップショット](#環境スナップショット) を参照。

## 実機シナリオ（§18.1–18.2）

| シナリオ                                                 | 実施 | 結果 / メモ |
| -------------------------------------------------------- | ---- | ----------- |
| §18.1 基本（role 命名・context 拒否・CLI 入口）          | ☐    |             |
| §18.2 Sync review（handoff → reviewer → requester 復帰） | ☐    |             |
| §18.2 Async handoff + result + completion                | ☐    |             |

## 未確認・残存リスク

- （例: §11.4 実機 TOCTOU、特定 Herdr バージョンでの prompt stalled）

## 実機知見（Herdr CLI 挙動）

- **Herdr 0.9.5 での `herdr pane rename` の構文制約（Issue #74 / RV028）**:
  - `Usage: herdr pane rename [OPTIONS] <PANE_ID> [LABEL]...` であり、位置引数 `<PANE_ID>` が最優先で解釈される。
  - `--` を付与して `pane rename -- <pane_id> <label>` と呼ぶと、`--` が PANE_ID と見なされて `pane "--" not found` で失敗する。
  - 同様に `--clear` を先頭に置いて `pane rename --clear -- <pane_id>` と呼ぶと、`--clear` が PANE_ID と見なされて失敗する。
  - ラベル設定は `herdr pane rename <pane_id> <label>`、クリアは `herdr pane rename <pane_id> --clear` が実機仕様。
  - PANE_ID 後の引数で特別扱いされるのは `--clear` のみ（`--help` / `-h` / `-x` はラベル扱い。0.9.5 実測）。Orchestrator 側では将来のオプション追加や誤認防止の予防措置として、先頭の `--` を自動除去して設定する。
  - 将来 Herdr のバージョン更新やサブコマンド構文改訂時に再確認を推奨。
- **`role clear` 時の兼任ペイン残存ロール UI ラベル保護（Issue #79）**:
  - ローカルとグローバルを兼任するペインで片側を `role clear` した場合、他スコープの残存ロール表示名を維持・再同期し、誤消去を防止（CW-22）。
  - `sync_herdr_pane_label` の実行結果を辞書（`synced`, `action`, `label`, `error`）として返却し、`role set` / `role clear` の JSON 出力で連動状況を可視化。
  - 残存ロール検索時は、未初期化 Git リポジトリからの `role clear --global` 実行時でもクラッシュしないよう例外を安全に捕捉し（CW-23 / RV048 B-1）、解除対象ロールの登録時ディレクトリ（`target_meta.cwd`）を優先してローカルスコープを解決（RV048 P2-1）。

## 移行（§19）

| Phase                | 完了 | メモ                                                          |
| -------------------- | ---- | ------------------------------------------------------------- |
| 1 中央 Skill 並行    | ☑    | 中央 Skill + repository-workflow + `.review-inbox`（本 repo） |
| 2 sync handoff       | ☐    |                                                               |
| 3 callback           | ☐    |                                                               |
| 4 Herdr CLI 重複削除 | ☐    |                                                               |
| 5 回帰               | ☐    |                                                               |
