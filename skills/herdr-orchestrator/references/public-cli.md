# Public CLI（固定 surface）

正本: [§6.5](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-5)、[§14.2](../../../docs/herdr-orchestrator/07-skill-and-implementation.md#sec-14-2)

リポジトリ内入口:

```text
skills/herdr-orchestrator/scripts/herdr-orchestrator
```

| コマンド                                                  | 用途                                                                           |
| --------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `handoff`                                                 | sync / async 配送（`--mode`, `--worker-role`, `--task-type`, `--instruction`） |
| `task-status` / `task-result`                             | Registry 読取                                                                  |
| `result-submit`                                           | worker 結果登録（async では completion 連鎖の best-effort）                    |
| `resume-task` / `reset-completion` / `recover-completion` | completion 再試行・復旧                                                        |
| `doctor`                                                  | 環境 + stale dispatch 候補                                                     |
| `reconcile-dispatch`                                      | dispatch 復旧（§6.12）                                                         |

Registry JSON の直接編集は禁止。内部 writer は `scripts/task-registry.py`（Agent 非公開）。
