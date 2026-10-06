# Herdr Orchestrator 設計書

本設計書はテーマごとに分割して管理する。章番号は旧版との対応と相互参照のため維持している。

実装の作業順序と完了条件は[実装 INDEX](herdr-orchestrator-implementation-index.md)にまとめる。各仕様の正本は以下の設計章とする。

## 読み進め方

全体像から実装・運用へ進む順に並べている。Task Registry と状態遷移は実装時の基準となるため、handoff や workflow を読む前に確認する。

| 順序 | ファイル                                                                                                 | 収録章 | 主な内容                                     |
| ---- | -------------------------------------------------------------------------------------------------------- | ------ | -------------------------------------------- |
| 1    | [概要・設計原則・コンポーネント](herdr-orchestrator/01-foundation.md#sec-1)                              | 1–3    | 概要・設計原則・コンポーネント               |
| 2    | [Role と Task Contract](herdr-orchestrator/02-roles-and-task-contract.md#sec-4)                          | 4–5    | Role と Task Contract                        |
| 3    | [Task Registry と状態管理](herdr-orchestrator/03-task-registry.md#sec-6)                                 | 6      | Task Registry と状態管理                     |
| 4    | [Handoff と Requester 識別](herdr-orchestrator/04-handoff-and-requester.md#sec-7)                        | 7–8    | Handoff と Requester 識別                    |
| 5    | [Completion・通知・復旧](herdr-orchestrator/05-completion-and-recovery.md#sec-9)                         | 9      | Completion・通知・復旧                       |
| 6    | [Result・Worker Resolution・レビュー workflow](herdr-orchestrator/06-results-and-workflows.md#sec-10)    | 10–12  | Result・Worker Resolution・レビュー workflow |
| 7    | [既存実装整理・Skill 構成・承認設計・中央管理](herdr-orchestrator/07-skill-and-implementation.md#sec-13) | 13–16  | 既存実装整理・Skill 構成・承認設計・中央管理 |
| 8    | [導入・動作確認・Repository 移行](herdr-orchestrator/08-adoption-and-verification.md#sec-17)             | 17–19  | 導入・動作確認・Repository 移行              |
| 9    | [バージョン管理・更新運用・禁止事項・将来構成](herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20)  | 20–24  | バージョン管理・更新運用・禁止事項・将来構成 |

## 基本方針

- Herdr の操作仕様は Herdr 公式 Skill / CLI を正とする。
- Orchestrator は task contract、routing、状態管理、handoff、result の共通規約を担う。
- Repository 固有の業務 workflow は個別 repository 側に残す。

章番号参照は分割ファイル間でリンクされている。設計内容を変更するときは [AGENTS.md](../AGENTS.md) の文書管理ルールに従う。

Herdr Orchestrator の設計・実装をレビューするときは、[エージェントレビューガイドライン](herdr-agent-review-guidelines.md)を参照する。
