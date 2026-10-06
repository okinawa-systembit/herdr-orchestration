---
name: herdr-orchestration
description: >-
  Central Herdr orchestration Skill (Task Registry, role routing, handoff). Operate
  via the herdr-orchestrator CLI. Distinct from the official herdr Skill. Not the
  simple two-agent inbox prototype.
---

# herdr-orchestration（中央 Skill）

実装の作業順序と設計書パスは [references/design-links.md](references/design-links.md)（リポジトリ同梱時は `docs/` 配下を正本とする）。**開発 clone** の PATH・Skill 配置: [references/dev-local-install.md](references/dev-local-install.md)。

## Agent の原則

- Herdr 操作は **herdr** 公式 Skill の意味論に従う。
- Registry 更新は **`herdr-orchestrator` public CLI** 経由のみ（JSON 直接編集禁止）。
- `HERDR_ENV=1` の pane 以外では handoff 等を実行しない。

## Public CLI

install 後は PATH 上の **`herdr-orchestrator`**（`~/.local/bin` → `~/.agents/skills/herdr-orchestration/scripts/herdr-orchestrator`）。開発・配布で同一。未 install 時のソース: `skills/herdr-orchestrator/scripts/herdr-orchestrator`。

コマンド一覧: [references/public-cli.md](references/public-cli.md)（正本は設計 [§6.5](../../docs/herdr-orchestrator/03-task-registry.md#sec-6-5)）。

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
