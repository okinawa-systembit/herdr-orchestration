---
name: herdr-orchestration
description: >-
  Central Herdr orchestration Skill (Task Registry, role routing, handoff).
  Use when the user instructs to delegate, hand off, or request tasks to other roles/panes via Herdr
  (e.g., 「herdr経由」「herdrで」「ハーダーで」「herdrオーケストラ経由で」
  「レビュー依頼をして」「〜に依頼して」「〜に頼んで」「委託して」)
  or instructs role configuration / scope modification
  (e.g., 「ロールを設定して」「ロールに設定して」「ロールを割り当てて」「グローバルに変更して」「ロールを解除して」).
  IMPORTANT: When the user asks to "依頼して" (request/delegate to another role), DO NOT perform
  the task (such as code review or investigation) yourself in the current pane;
  always hand it off to the target role via herdr-orchestrator.
  NOTE: If the user explicitly asks for official Herdr commands/behavior
  (e.g., 「herdr公式で」「公式Herdrで」), DO NOT use this skill; instead read the
  official Herdr skill installed globally (e.g., ~/.agents/skills/herdr/SKILL.md).
  If the user specifically asks for the official documentation (e.g., 「公式ドキュメントで」
  「herdrのドキュメントで」), also read the official documentation at https://herdr.dev/llms.txt.
---

# herdr-orchestration（中央 Skill）

実装の作業順序と設計書パスは [references/design-links.md](references/design-links.md)（リポジトリ同梱時は `docs/` 配下を正本とする）。**開発 clone** の PATH・Skill 配置: [references/dev-local-install.md](references/dev-local-install.md)。

## Agent の原則

- **発動条件とセルフ作業の禁止**:
  - ユーザーから「herdr経由」「herdrで」「ハーダーで」「herdrオーケストラ経由で」が指定された場合、またはHerdr環境下の登録ロール宛てに「レビュー依頼をして」「〜に依頼して」「〜に頼んで」「委託して」と指示された場合は本スキルを発動する。
  - **他者への委託（「依頼をして」等）を指示された場合、現在の自ペインでその作業（コードレビューや調査・実装など）を直接セルフ実行することは厳禁**。必ず本スキルの `herdr-orchestrator handoff` を使って対象 Worker ロールへ委託すること。
  - ユーザーから「ロールを設定して」「ロールをグローバルに変更して」「ロールを解除して」等の Role Binding 操作を指示された場合も本スキルを発動する。
- **委託時の登録ロール柔軟マッチングと曖昧時の人間確認**:
  - `handoff` を実行する前に、必ず **`herdr-orchestrator role show`** を実行して現在有効な登録ロール一覧を確認する。
  - ユーザーの指示語（例: 「レビュー依頼」「調査依頼」「ログ調べて」）に対し、登録一覧の中から最も意味の合致する登録ロール名（例: `レビュワー`、`reviewer`、`調査担当`、`ログ調査`）を AI が判断して `--to <role>` または `--worker-role <role>` に指定する（完全一致で安全に届かせる）。
  - **曖昧時の人間確認**: 指示語と完全一致するロールがなく、かつ意味的に合致する候補が 0 件または 2 件以上ある場合は、AI の独断で handoff せず、`role show` の一覧を示して人間に宛先を確認すること。
- **ロール設定・スコープ変更時の人間向けラベル（`--label`）明示と2段階移行手順**:
  - ユーザーからロール設定（ローカル / グローバル問わず）を指示された場合、CLI のデフォルト流用に頼らず、人間が Herdr UI（タブやサイドバー）上で一目で役割を識別できるよう、必ず **`--label "<人間向け日本語表示名>"`**（例: `--label "実装調査"`, `--label "チケット管理 (共通)"`）を明示して `herdr-orchestrator role set` を実行すること。
  - ロールをローカルからグローバルへ（またはその逆へ）変更する場合は、UI ラベルの途切れを防ぎつつ同名重複による配送停止（`worker_role_scope_ambiguous`）を防止するため、以下の**2段階移行手順**を必ず実行すること：
    1. **新スコープで登録**: `herdr-orchestrator role set <role> --label "<表示名>" [--global]` を実行する。
    2. **旧スコープで必ず解除**: 続いて `herdr-orchestrator role clear <role> [--global]` を実行して旧登録を解除する（Orchestrator の残存ロール保護により、旧スコープ解除時も新スコープの UI ラベルは安全に維持される）。※ 旧スコープを解除し忘れると同名ロール重複となり、以降の `handoff` が `worker_role_scope_ambiguous` で確認停止するため必須。
  - 完了報告時は、設定した論理ロール名と Herdr UI に反映した表示名（ラベル）の両方を人間に明示すること。
- **公式 Herdr / 公式ドキュメント指定時の切り替え**:
  - ユーザー指示に「herdr公式で」「公式Herdrで」など「公式」が含まれる場合は、本 Orchestrator CLI は使わず、グローバルにインストールされている公式 **herdr** Skill（`~/.agents/skills/herdr/SKILL.md` 等）を読み込んで raw の Herdr 操作を行う。
  - 「公式ドキュメントで」「herdrのドキュメントで」などドキュメント自体が明示された場合は、公式 LLM ドキュメント（[https://herdr.dev/llms.txt](https://herdr.dev/llms.txt)）を追加で参照して回答・対応する。
- **CLI 経由の厳守**:
  - Herdr 操作は **herdr** 公式 Skill の意味論に従う。
  - Registry 更新は **`herdr-orchestrator` public CLI** 経由のみ（JSON 直接編集禁止）。
  - `HERDR_ENV=1` の pane 以外では handoff 等を実行しない。

## Public CLI

install 後は PATH 上の **`herdr-orchestrator`**（`~/.local/bin` → `~/.agents/skills/herdr-orchestration/scripts/herdr-orchestrator`）。開発・配布で同一。未 install 時のソース: `skills/herdr-orchestrator/scripts/herdr-orchestrator`。

コマンド一覧およびクラッシュ再起動後のタスク状態確認手順: [references/public-cli.md](references/public-cli.md)（正本は設計 [§6.5](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-5)）。

## 責務境界

| 層                         | 担当                                                                                |
| -------------------------- | ----------------------------------------------------------------------------------- |
| 中央 `herdr-orchestration` | role 命名、handoff、Registry、public CLI、dispatch/completion 復旧                  |
| herdr 公式 Skill           | pane / agent / prompt の意味論                                                      |
| repository Skill           | Review Scope・成果物形式・[worker/requester 受信手順](references/worker-receive.md) |

受信側必須手順（複製せずリンク）:

- Worker: [references/worker-receive.md](references/worker-receive.md)（§11.4）
- Requester（completion 後）: [references/requester-receive.md](references/requester-receive.md)

Handoff 概要: [references/handoff.md](references/handoff.md)。本リポジトリ: [repo-workflow](../../.agents/skills/repo-workflow/SKILL.md) / [repo-review](../../.agents/skills/repo-review/SKILL.md)。

導入・移行・§18 記録: [references/adoption.md](references/adoption.md)（`install/setup.sh`）。
