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

<a id="review-principles"></a>

## レビュー判定・品質基準

### 1. 重大度と Blocking 基準

既存の [レビューガイドライン](../../../docs/herdr-agent-review-guidelines.md) に準拠し、重大度と Blocking 判定を区別する。

| 重大度 | 基準                                                                       | Blocking 判定                                                                      |
| :----- | :------------------------------------------------------------------------- | :--------------------------------------------------------------------------------- |
| **P0** | データ破壊、重大なセキュリティ問題、プロセス停止不能など、直ちに対応が必要 | **Blocking**                                                                       |
| **P1** | 主要機能の破綻、重大な状態不整合、誤配送、契約違反など                     | **Blocking**                                                                       |
| **P2** | 特定条件下でのみ発生する不具合、実運用に影響する設計課題                   | 原則 **Non-blocking**（リリース前修正推奨。重大影響が証明できる場合のみ Blocking） |
| **P3** | 軽微な改善、保守性向上、可読性、ドキュメントの表現統一など                 | **Non-blocking**（理由なく Blocking 扱い禁止）                                     |

### 2. 指摘テンプレートと確信度

各指摘には必ず以下の項目を記載する。

- **対象**: `path/to/file:L123`
- **問題**: 何が間違っているか、なぜ問題なのか
- **発生条件**: どのようなシーケンス・引数・環境条件下で発生するか
- **影響**: 発生した場合に具体的に何が起きるか（データ破壊、不整合、クラッシュ等）
- **根拠**: 該当コード、設計書章節アンカー、公式仕様、テスト結果など
- **修正方針**: 推奨する具体的な改訂案
- **確信度**:
  - **高**: コード、仕様書、テスト、または最小再現によって具体的に裏付けられている。
  - **中**: 論理的な根拠はあるが、一部の実行時条件やエッジケースが未確認。
  - **低**: 追加調査が必要。**原則として確定的不具合とせず「要確認事項」へ分類する。**

### 3. 誤検出の抑制ルール

以下は確定的不具合（P0〜P2）として報告せず、「要確認事項」に分類するか除外する。

1. 具体的な発生条件を論理的・客観的に説明できない問題。
2. 他の呼び出し元や既存実装のガード処理によって既に防止されている問題。
3. 設計書や公式仕様として許容されている動作・障害モデル（過大保証の強要禁止）。
4. 具体的な根拠のない一般的なベストプラクティスやスタイル論。
5. 現在の要件・段階に関係しない将来拡張。
6. 単なる好みや主観的表現の差異。

### 4. 再レビュー時の検証方針

再レビュー時は以下を必須で実施する。

1. **前回指摘の再検証**: 各指摘を最新コードで検証し、以下のいずれかに分類する。
   - `対応済み` / `部分対応` / `未対応` / `前回指摘の誤り` / `検証不能`
   - ※ 前回の指摘が常に正しかったと盲信せず、反証や意図的仕様がないか検証する。
2. **回帰不具合の確認**: 修正によって別の問題が発生していないか、関連する状態遷移やエラー処理、既存テストが破綻していないかを確認する。
3. **新規問題の探索**: 新たな差分および周辺コードについて、前回見落とした問題がないか探索する（前回と同じ指摘を根拠なく繰り返さない）。

---

<a id="review-request-body"></a>

## review-request.md（requester）

`.review-inbox/review-request.example.md` を正本として更新する。

- 必須項目: 対象リポジトリ、基準ブランチ/コミット、対象コミット/ブランチ、レビュー種別、変更目的、重点確認事項、テスト結果。
- 再レビュー時: 前回レビューの参照先を必ず記載する。

---

<a id="review-response-body"></a>

## review-response.md（reviewer）

先頭行 **固定（verbatim）**: `レビュー対応をして`  
テンプレート: `.review-inbox/review-response.example.md`

構成:

1. `レビュー対応をして`
2. `## 判定`（GO / 要修正 / NO-GO）
3. `## 総評`
4. `## Blocking 指摘`
5. `## Non-blocking 指摘`
6. `## 要確認事項`
7. `## 再レビュー結果`（再レビュー時のみ）
8. `## テスト・検証結果`
9. `## 調査範囲と未確認事項`

※ 指摘が 0 件の場合も、確認範囲（ファイル・関数）と実施した検証を明記する（「問題が発見されなかったこと」と「問題が存在しないこと」を混同しない）。

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
