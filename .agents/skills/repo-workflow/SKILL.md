---
name: repo-workflow
description: >-
  Repository-side Herdr Orchestrator workflow: sync review handoff, worker
  Registry verification (§11.4), result-submit, and requester task-result
  readback. Use with central herdr-orchestrator CLI; not the simple two-agent
  inbox prototype alone.
---

# Herdr Orchestrator — repository workflow（本リポジトリ）

中央 Skill: [skills/herdr-orchestrator/SKILL.md](../../../skills/herdr-orchestrator/SKILL.md)

## 前提

- `HERDR_ENV=1` の Agent pane からのみ Orchestrator CLI を実行する。
- Herdr 操作の意味論は **herdr** 公式 Skill に従う（CLI 説明をここに複製しない）。
- Registry 更新は **herdr-orchestrator** public CLI のみ。
- 実行前に中央 Skill を **`~/.agents/skills/herdr-orchestration`** へ install 済みであること（開発: `npm run install:orchestrator-dev`）。CLI は **`herdr-orchestrator`**（`~/.local/bin` → install 先 scripts）。

```bash
herdr-orchestrator --help
```

## Requester: sync レビュー依頼（§12.1）

**本リポジトリのレビュー手順（Scope・inbox・result-submit 規約）**: [repo-review SKILL.md](../repo-review/SKILL.md)

1. 同 Skill に従い `.review-inbox/review-request.md` を更新する。
2. 同一 worktree・requester pane から `herdr-orchestrator handoff --mode sync --worker-role reviewer …`（instruction は inbox 参照の短文）。
3. sync 完了後 `task-result <task.id>`。requester 受信は [requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md)。

## Worker: 受信（§11.4）

[worker-receive.md](../../../skills/herdr-orchestrator/references/worker-receive.md) に従う。

- prompt 後: `task-status` → dispatch poll → context 一致 → 作業 → `result-submit`。
- `dispatch_not_ready` 時は同一 payload で retry。

## Requester: completion 後（§11.4 / §9.12）

[requester-receive.md](../../../skills/herdr-orchestrator/references/requester-receive.md) に従う。

- `task-status` / `task-result` で result terminal を確認してから inbox や review response を開始する。

## 復旧

- stale dispatch: `doctor` → `reconcile-dispatch`（設計 §6.12）。
- completion 失敗: `reset-completion` + `resume-task` または `recover-completion`（初回 dispatch 失敗 terminal は reset 不可）。

## 参照

- [public-cli.md](../../../skills/herdr-orchestrator/references/public-cli.md)
- [handoff.md](../../../skills/herdr-orchestrator/references/handoff.md)
- [roles.md](../../../skills/herdr-orchestrator/references/roles.md)
