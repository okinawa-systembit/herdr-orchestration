# Logical roles（初期4 role）

正本: [§4](../../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4)（グローバル Skill 単体配置時は [design-links.md](design-links.md)）

| Role           | 用途                 |
| -------------- | -------------------- |
| `design`       | 設計・仕様           |
| `reviewer`     | コード・設計レビュー |
| `implementer`  | 実装と実装伴うテスト |
| `orchestrator` | 委譲・進行確認       |

`agent.name` は live routing locator（永続 ID ではない）。
