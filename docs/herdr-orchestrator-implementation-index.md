# Herdr Orchestrator 実装 INDEX

> [設計書の目次](herdr-orchestrator-design.md) | [動作確認の正本](herdr-orchestrator/08-adoption-and-verification.md#sec-18)

この文書は実装エージェント向けの**作業順序と完了ゲート**を示す。機能の仕様、canonical schema、状態遷移、CLI の意味は各設計章を正本とし、ここには複製しない。実装中に矛盾を見つけた場合は、該当する設計章と検証項目を先に整合させてから実装する。

## 実装範囲と進め方

- 対象は [§4 の初期4 role](herdr-orchestrator/02-roles-and-task-contract.md#sec-4) を使う MVP。専任 `tester` / `researcher`、task cancellation CLI、`task-list` は初期実装に含めない。
- 中央 Skill と wrapper のソースを、このリポジトリの `skills/herdr-orchestrator/` に置く。[§14 の構成](herdr-orchestrator/07-skill-and-implementation.md#sec-14) に従い、`SKILL.md`、`scripts/herdr-orchestrator`、`scripts/task-registry.py`、`references/` を用意する。既存のリポジトリ固有 Skill への導入は最終段階で行う。
- Python 3.10 と標準ライブラリを基準にし、Herdr の操作仕様は利用中の公式 CLI / Skill で確認する（[§14.3](herdr-orchestrator/07-skill-and-implementation.md#sec-14-3)、[§20.2](herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20-2)）。
- 下表の順に実装し、各段階の完了ゲートを満たしてから次へ進む。各段階では、その機能に関係する [§18 の検証項目](herdr-orchestrator/08-adoption-and-verification.md#sec-18) を自動テストまたは実機確認に対応付け、結果を記録する。
- Agent への実送信を伴う試験は、テスト用の repository / worktree と Agent で行う。`uncertain` の自動再送、Registry JSON の直接編集、未検証の別 target への fallback は行わない（[§6](herdr-orchestrator/03-task-registry.md#sec-6)、[§11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4)）。

| 順序 | 実装単位                        | 主な成果物                                         | 完了ゲート                                                                                                                                    |
| ---- | ------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | 実行基盤と仕様確認              | Skill / CLI の骨格、テスト環境                     | 利用中 Herdr と公開 API を確認し、CLI が安全に失敗する                                                                                        |
| 1    | role・repository・worktree 解決 | 共通の名前生成と context 検証                      | 同一入力が同一 identity に解決され、曖昧な入力を拒否する                                                                                      |
| 2    | Task Registry                   | schema v1、4軸遷移、atomic writer                  | 競合・不正遷移・path 攻撃でも Registry が壊れない                                                                                             |
| 3    | 結果と読み取り CLI              | `task-status`、`task-result`、`result-submit`      | 正規 result のみ登録し、同一 payload の再登録が冪等になる                                                                                     |
| 4    | handoff と sync 完了待ち        | `handoff`、worker 受信手順                         | 最小 prompt を送り、Registry の result 確定で sync が終了する                                                                                 |
| 5    | dispatch 診断・復旧             | `doctor`、`reconcile-dispatch`                     | クラッシュ窓から未配送・結果不明を区別して復旧できる                                                                                          |
| 6    | async completion・復旧          | requester 再開、`resume-task`、reset / recover     | result 確定後のみ claim し、重複入力を防ぐ                                                                                                    |
| 7    | 中央 Skill と repository 接続   | `SKILL.md`、`references/`、repository 側の利用手順 | worker / requester が Registry を検証してから作業する                                                                                         |
| 8    | 導入・移行・総合確認            | installer、移行手順、検証記録                      | [§18](herdr-orchestrator/08-adoption-and-verification.md#sec-18) と [§19](herdr-orchestrator/08-adoption-and-verification.md#sec-19) を満たす |

## 0. 実行基盤と仕様確認

1. [§17.1–17.2](herdr-orchestrator/08-adoption-and-verification.md#sec-17-1) と [§20.2](herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20-2) に従い、CLI / server / 公式 Skill / API schema を確認する。Herdr を実装作業のために自動更新しない。
2. [§14.2](herdr-orchestrator/07-skill-and-implementation.md#sec-14-2) の wrapper と Registry writer の入口を作り、public CLI と内部 operation を分離する。
3. `HERDR_ENV=1` がない場合は Herdr を呼ばず **exit 非 0** で失敗させる（`doctor` も診断 JSON を stdout に出したうえで exit 1。Herdr CLI は呼ばない）。テストは実 Agent を使わずに CLI 結果と Registry 更新を検査できる構成を用意する。

**完了ゲート:** `--help` 等の公開入口が確認でき、環境未設定時に Herdr 操作も Registry 作成も起きない（[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 5・81）。

## 1. Role と作業 context（ライブラリ）

1. [§4.1](herdr-orchestrator/02-roles-and-task-contract.md#sec-4-1) の role / `agent.name` 規則と [§8.5](herdr-orchestrator/04-handoff-and-requester.md#sec-8-5) の requester 導出を共通関数にする。
2. [§11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0) の repository identity 正規化、worktree root 解決、全 remote の照合を実装する。handoff と submit は同じ関数を使う。
3. live Agent の名前・状態・context 取得と worktree / repository 照合を **`orchestrator/live_agent.py`** に集約する（`agent get` のみ。pane ID fallback なし）。handoff / `result-submit` からの **呼び出し配線**は段階 4 で行う。

**完了ゲート（段階 1）:** 命名・identity・worktree 単体テスト、および `live_agent` のモックテスト（[§18.1](herdr-orchestrator/08-adoption-and-verification.md#sec-18-1)、[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 114–125）。実 Herdr 上の worker 選定 E2E は段階 4 のゲートに含める。

## 2. Task Registry

1. [§6.2–6.3](herdr-orchestrator/03-task-registry.md#sec-6-2) の schema v1 と4軸の状態遷移を writer 一箇所に実装する。`task.id` は writer が生成する。
2. [§6.4–6.9](herdr-orchestrator/03-task-registry.md#sec-6-4) の file lock、atomic replace、permission、symlink 拒否、保持期間を実装する。handoff 実行 flock と短時間の Registry lock は区別する。
3. [§6.6](herdr-orchestrator/03-task-registry.md#sec-6-6) の内部 operation と [§6.13](herdr-orchestrator/03-task-registry.md#sec-6-13) の条件付き更新を実装し、同時実行時に一方だけが更新できるようにする。

**完了ゲート:** schema・状態遷移・競合・権限・path safety・cleanup をテストし、既存 JSON を不正入力で変更しない（[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 1–12・33・65・86）。

## 3. Result と読み取り CLI

1. [§6.5](herdr-orchestrator/03-task-registry.md#sec-6-5) の `task-status` / `task-result` を実装し、`task.id` の厳格検証と安全な `result.ref` 解決を行う。
2. [§10](herdr-orchestrator/06-results-and-workflows.md#sec-10) の Result Contract、submit 元の Agent / repository / worktree 検証、terminal result の冪等 replay を実装する。
3. `dispatch=pending` の早着 `result-submit` は retryable に拒否し、同一 payload での bounded retry と reconcile 後の再登録を受けられるようにする（[§10.2](herdr-orchestrator/06-results-and-workflows.md#sec-10-2)）。

**完了ゲート:** 正規結果、重複 submit、別 Agent / worktree、artifact 不在、path escape を検証する（[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 19–22・59・63・67–68・78–80）。

## 4. Handoff と sync

1. [§6.11](herdr-orchestrator/03-task-registry.md#sec-6-11) の順に `handoff` を実装し、wrapper 自身が handoff flock を保持する。`create` 前の入力 / live worker 検証を先に完了する。
2. [§11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4) の直前再確認、最小 Task Envelope、worker 側 Registry 検証を実装する。worker は `dispatch=pending` を期限付きで再確認してから作業する。
3. sync は [§6.14](herdr-orchestrator/03-task-registry.md#sec-6-14) の result 確定待機で終了させる。async は dispatch 確定後に return し、completion は手順 6 で追加する（[§7](herdr-orchestrator/04-handoff-and-requester.md#sec-7)）。

**完了ゲート:** prompt 前後の停止、worker 状態変化、早着 `pending`、結果未確定、sync timeout を検証する（[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 15a–15d・66・73・77・91–94・101・110–111）。

## 5. Dispatch 診断と復旧

1. [§6.12–6.13](herdr-orchestrator/03-task-registry.md#sec-6-12) の A / B / B' クラッシュ窓、`doctor`、`reconcile-dispatch` を実装する。
2. `uncertain` と確実な未配送を分け、未配送確定時の `--dispatch-reason` を必須にする。既定の A 手順では Registry を変更しない。prompt の blind resend は行わない。
3. reconcile 後に result 再登録が必要な場合は CLI で案内し、[§9.10](herdr-orchestrator/05-completion-and-recovery.md#sec-9-10) の requester 向け異常報告につなぐ。

**完了ゲート:** 生存 wrapper の flock 保持中は reconcile が更新せず、停止後は条件付き更新で一度だけ確定する（[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 74–75・83–90・93・98・105–107・113）。

## 6. Async completion と recovery

1. [§9.13–9.14](herdr-orchestrator/05-completion-and-recovery.md#sec-9-13) の eligibility / reset 条件を Registry の共通判定にし、`claim-completion` を原子的に実装する。
2. `result-submit` 成功後の best-effort completion、`resume-task`、`reset-completion`、`recover-completion` を [§9](herdr-orchestrator/05-completion-and-recovery.md#sec-9) に従って実装する。requester の直前再確認と最小 prompt を適用する。
3. prompt を受けた requester は [§9.12](herdr-orchestrator/05-completion-and-recovery.md#sec-9-12) に従い Registry を読み、result と context を確認してから後続処理を始める。配送結果不明は自動再送しない。

**完了ゲート:** 並行 claim、`working` / context mismatch、`uncertain`、stuck recovery、初回配送失敗時の reset 拒否を検証する（[§18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-3) の項目 23–32・45–57・60–64・69–70・95–97・100・108–109・112）。

## 7. 中央 Skill と repository 接続

1. [§14](herdr-orchestrator/07-skill-and-implementation.md#sec-14) に従い、中央 `SKILL.md` を小さく保ち、role / handoff 規約を `references/` に分ける。Herdr の CLI 操作説明を複製しない。
2. repository 固有 Skill から固定 public CLI を呼び、worker の受信側検証・結果保存・`result-submit` と requester の Registry 再読を接続する（[§11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4)、[§12](herdr-orchestrator/06-results-and-workflows.md#sec-12)）。
3. repository 固有レビュー（本 repo: `repo-review`）は [§19](herdr-orchestrator/08-adoption-and-verification.md#sec-19) に従い他 repo へ展開する。

**完了ゲート:** 公式 Herdr Skill と中央 Skill の責務が分かれ、sync review と async handoff をテスト用 repository で実行できる（[§18.1–18.3](herdr-orchestrator/08-adoption-and-verification.md#sec-18-1)）。

## 8. 導入と総合確認

1. [§16](herdr-orchestrator/07-skill-and-implementation.md#sec-16) と [§17](herdr-orchestrator/08-adoption-and-verification.md#sec-17) に従い、ユーザーグローバル Skill への導入手順と installer を用意する。各製品の配置先は導入時の公式仕様で確認する。
2. [§19 の Phase 1–5](herdr-orchestrator/08-adoption-and-verification.md#sec-19) に従い、中央方式と既存方式の並行確認後に repository ごとに移行する。既存レビュー成果物の形式は repository 側に残す。
3. [§18](herdr-orchestrator/08-adoption-and-verification.md#sec-18) の全項目を確認し、未確認項目・残存リスク・Herdr CLI / server / Skill のバージョンを記録する。

**完了ゲート:** 対象 repository で sync review、async result、requester 再開、復旧を再現でき、移行前のレビュー workflow も回帰しない。

## MVP 実装状態（本リポジトリ）

段階 0–8 の **CLI・Registry・中央 Skill・installer** は `skills/herdr-orchestrator/` に実装済み。

| 確認                          | 入口                                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------------------- |
| 自動テスト（段階 0–8 ゲート） | リポジトリ root で `npm run test:orchestrator`                                           |
| 導入前一括確認                | `npm run verify:orchestrator`                                                            |
| グローバル Skill 導入         | `skills/herdr-orchestrator/install/setup.sh` または `npm run install:orchestrator-skill` |
| §18 実機・§19 移行の記録      | [herdr-orchestrator-verification-log.md](../herdr-orchestrator-verification-log.md)      |
| repository workflow 例        | `.agents/skills/repo-workflow/SKILL.md`                                                  |

**残作業（実装 INDEX 外の運用）:** [§18.1–18.2](herdr-orchestrator/08-adoption-and-verification.md#sec-18-1) の Herdr 実機シナリオを検証ログに記録する。[§19](herdr-orchestrator/08-adoption-and-verification.md#sec-19) Phase 1–5 は repository ごとに並行検証後に移行する。
