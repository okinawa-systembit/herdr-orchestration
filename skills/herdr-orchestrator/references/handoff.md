# Handoff 概要

正本: [§7–8](../../../docs/herdr-orchestrator/04-handoff-and-requester.md#sec-7)、[§6.11](../../../docs/herdr-orchestrator/03-task-registry.md#sec-6-11)（単体配置時は [design-links.md](design-links.md)）。

- sync: Registry `result.status` terminal で完了（`agent prompt --wait` だけに依存しない）。
- async: dispatch 確定後 return。completion は `result-submit` 連鎖または `resume-task`。
- 配送安全: [§11.4](../../../docs/herdr-orchestrator/06-results-and-workflows.md#sec-11-4)（最小 prompt + 受信側 Registry 検証）。

`herdr-orchestrator handoff` は public CLI で提供（段階 4+）。repository 側は [worker-receive.md](worker-receive.md) / [requester-receive.md](requester-receive.md) を Skill に組み込む。
