# Herdr Orchestrator — §18 検証記録（テンプレート）

> 正本チェックリスト: [§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3)  
> 実装 INDEX: [段階8](herdr-orchestrator-implementation-index.md#8-導入と総合確認)

運用者が実機確認後に追記する。項目の仕様本文は設計書を正本とし、ここには **結果だけ**を書く。

## 環境スナップショット

| 項目                            | 値                                                                              |
| ------------------------------- | ------------------------------------------------------------------------------- |
| 記録日                          | 2026-10-06                                                                      |
| 対象 repository                 | herdr-orchestration                                                             |
| Herdr CLI (`herdr --version`)   | herdr 0.9.3                                                                     |
| Herdr server / workspace        | w11（開発環境）                                                                 |
| 公式 herdr Skill 導入方法       | （各環境の §17.2 に従い記録）                                                   |
| herdr-orchestrator Skill 配置先 | リポジトリ同梱 + `install/setup.sh` または `npm run install:orchestrator-skill` |
| `npm run test:orchestrator`     | 149 passed（段階8 GO レビュー時）                                               |
| `npm run verify:orchestrator`   | OK（2026-10-06）                                                                |

## 自動テスト対応（CLI / Registry）

`npm run test:orchestrator` がカバーする §18.3 項目は各 `tests/test_orchestrator_phase*.py` ファイル先頭コメントを参照。

最新ローカル結果:

```text
149 passed (stage 0–8 gate tests)
npm run verify:orchestrator: OK
Git: 922f044 (verify tooling + §18 automated snapshot)
Inbox: 段階0–8 GO (2026-10-06 10:29)
```

## 実機シナリオ（§18.1–18.2）

| シナリオ                                                 | 実施 | 結果 / メモ |
| -------------------------------------------------------- | ---- | ----------- |
| §18.1 基本（role 命名・context 拒否・CLI 入口）          | ☐    |             |
| §18.2 Sync review（handoff → reviewer → requester 復帰） | ☐    |             |
| §18.2 Async handoff + result + completion                | ☐    |             |

## 未確認・残存リスク

- （例: §11.4 実機 TOCTOU、特定 Herdr バージョンでの prompt stalled）

## 移行（§19）

| Phase                | 完了 | メモ                                                          |
| -------------------- | ---- | ------------------------------------------------------------- |
| 1 中央 Skill 並行    | ☑    | 中央 Skill + repository-workflow + `.review-inbox`（本 repo） |
| 2 sync handoff       | ☐    |                                                               |
| 3 callback           | ☐    |                                                               |
| 4 Herdr CLI 重複削除 | ☐    |                                                               |
| 5 回帰               | ☐    |                                                               |
