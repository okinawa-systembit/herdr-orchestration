# herdr-orchestration レビュー — 参照

## 役割と pane（例）

Herdr 上の live 名は環境依存。logical role は handoff の `reviewer` / requester pane。

| 論理 role               | 例（この workspace）       | 備考                              |
| ----------------------- | -------------------------- | --------------------------------- |
| requester / implementer | `${HERDR_WORKSPACE_ID}:p1` | handoff 実行 pane                 |
| reviewer worker         | `${HERDR_WORKSPACE_ID}:p2` | `--worker-role reviewer` の解決先 |

`herdr agent list` で `agent.name` と pane を確認。Registry の `worker.agent_name` と reviewer 自身の live 名が一致すること（§11.4）。

---

<a id="review-scope"></a>

## レビュー Scope チェックリスト

- [ ] 変更が [実装 INDEX](../../../docs/herdr-orchestrator-implementation-index.md) の段階と一致
- [ ] Task Registry / 状態遷移の正本は [03-task-registry.md](../../../docs/herdr-orchestrator/03-task-registry.md) に沿う
- [ ] Herdr 公式仕様と独自設計の境界（[レビューガイドライン](../../../docs/herdr-agent-review-guidelines.md) §2–4）
- [ ] public CLI のみ Registry 更新（JSON 手編集なし）
- [ ] §15: 破壊的操作を allowlist 前提にしない
- [ ] テスト: `npm run test:orchestrator`（claim は request / response に記載）
- [ ] Markdown 変更時: 設計書なら `npm run format:md` / `check:md`（AGENTS.md）

---

<a id="review-request-body"></a>

## review-request.md（requester）

`.review-inbox/review-request.example.md` をコピー。

必須: リポジトリ path、ブランチ/コミット/差分、重点観点、テスト結果（pass 数）。

---

<a id="review-response-body"></a>

## review-response.md（reviewer）

先頭行 **固定**: `レビュー対応をして`

```markdown
レビュー対応をして

## 判定

GO | 要修正 | NO-GO（Blocking N件）

## 指摘

1. （P1/P2、ファイル、 actionable）

## テスト・検証結果

- …

## 補足

…
```

---

<a id="review-index"></a>

## INDEX.md

`# レビュー INDEX` 直下に **新しい見出しを先頭追記**（時刻 — テーマ、判定、一行要約）。

---

<a id="human-report"></a>

## 人間報告（implementer）

```markdown
## レビュー対応完了

- 依頼: `.review-inbox/review-response.md`
- 対応概要: …
- INDEX: 最新行参照
- 未対応: なし | …
```

---

## Herdr prompt（補助・1 行のみ）

sync handoff が prompt 配送するのが正。足りない場合のみ:

```bash
herdr agent prompt "${HERDR_WORKSPACE_ID}:p2" '.review-inbox/review-request.md を読め'
```

```bash
herdr agent prompt "${HERDR_WORKSPACE_ID}:p1" '.review-inbox/review-response.md を読め'
```

長文を prompt に載せない。
