# バージョン管理・更新運用・禁止事項・将来構成

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-20"></a>

## 20. Herdr バージョン・公式 Skill 管理

<a id="sec-20-1"></a>

### 20.1 現在の実機確認環境

2026-10-01 の WSL 実機確認では次の状態である。これは参考環境であり、`herdr-orchestrator` の固定バージョン要件ではない。

```text
Platform: WSL / Linux
Herdr: 0.9.3
Update channel: stable
Installation: direct install
Previous version: 0.8.x
Update method: herdr update
```

実際に `herdr update` により 0.8.x から 0.9.3 へ更新され、更新後も `herdr channel show` は `stable` であることを確認した。したがって、社内運用では GitHub Releases 等の表示を独自の最新版判定基準とせず、Herdr 自身の configured update channel と update mechanism を正とする。

<a id="sec-20-2"></a>

### 20.2 操作仕様の確認

Herdr の操作仕様を確認する際は、現在インストールされている Herdr を基準とする。

```bash
herdr --version
herdr channel show
herdr status
herdr --help
herdr --skill
```

必要に応じて Socket API schema も確認する。

```bash
herdr api schema
herdr api schema --json
```

`herdr --skill` で現在の Herdr release に対応する公式 Skill を参照する。公式 Skill を CLI / server のバージョン差異を自動吸収する互換レイヤーとはみなさない。

<a id="sec-20-3"></a>

### 20.3 バージョン確認

Herdr は background version check を持つため、`herdr-orchestrator` は通常の handoff ごとに外部サービスへ最新版を照会しない。

また、GitHub Releases 等を直接参照して `latest stable` を独自判定しない。更新可否の基準は Herdr 自身の configured channel と version check / update mechanism とする。

Agent が明示的にバージョン確認を行うのは、次の場合に限定する。

- ユーザーが Herdr / Skill の更新状況確認を依頼した場合
- Herdr CLI / server / Skill の仕様不一致が疑われる場合
- セットアップ・診断・定期メンテナンスを実行する場合

確認時は少なくとも次を区別する。

```text
installed CLI version
configured update channel
running server status/version
installed release-matched Skill
```

`herdr update` は確認専用コマンドではなく更新操作であるため、最新版確認だけを目的として自動実行しない。

<a id="sec-20-4"></a>

### 20.4 更新通知

Herdr 自身の background version check / update notice を通常運用の更新通知として利用する。`herdr-orchestrator` は handoff の実行経路で重複通知を実装しない。

明示的な診断・メンテナンス中に更新必要性が判明した場合は、自動更新せずユーザーへ通知し、更新実行はユーザーまたは管理者の判断とする。

<a id="sec-20-5"></a>

### 20.5 更新方法

直接インストール版では configured channel に対して公式の更新操作を利用する。

```bash
herdr channel show
herdr update
```

Homebrew / mise / Nix 等で管理されている場合は各 package manager の更新方法を使用し、`herdr update` を無条件に実行しない。

更新後は CLI と稼働中 server の状態が一致しているとは限らないため、新しい server-side 機能を利用する前に次を確認する。

```bash
herdr --version
herdr status
```

必要な server 機能が旧 server のままでは利用できない場合は、現在の公式手順に従って server を再起動する。

<a id="sec-20-6"></a>

### 20.6 Update Channel

社内標準は原則 `stable` とする。

```bash
herdr channel show
```

preview は stable に未反映の修正を明示的に検証する場合のみ利用する。中央 Skill が自動的に preview へ切り替えない。

<a id="sec-20-7"></a>

### 20.7 推奨運用

```text
通常の handoff
  → version check しない

Herdr起動・通常利用
  → Herdr自身の background version check / update notice を利用

明示的な診断・メンテナンス
  → version / channel / server status / release-matched Skill を確認
  → 必要ならユーザーへ更新を案内
```

将来的に社内共通の定期確認が必要になった場合も、`herdr-orchestrator` の handoff 本体ではなく、独立した setup / doctor / maintenance workflow として実装する。

---

<a id="sec-21"></a>

## 21. 更新運用

<a id="sec-21-update-herdr"></a>

### Herdr 更新

Herdr 更新時は Herdr 公式 Skill も対応バージョンへ更新する。

Orchestrator 側で Herdr CLI の変更を追従実装しない。

<a id="sec-21-update-orchestrator"></a>

### herdr-orchestrator 更新

以下のみ中央管理する。

- role
- routing
- handoff
- sync / async policy
- callback policy

<a id="sec-21-update-repository"></a>

### Repository 更新

以下は各 repository で管理する。

- review policy
- CI
- RNNN
- Finding
- Scope
- Review Response

---

<a id="sec-22"></a>

## 22. 設計上の禁止事項

以下を原則禁止する。

- Herdr 公式 Skill の社内 fork を通常運用する。
- Herdr CLI の操作仕様を `herdr-orchestrator` に複製する。
- 中央 Skill を利用するためだけに個別 repository を変更する。
- requester を無条件で `main` へ rename する。
- pane label を標準の Agent routing key とする。
- timeout / stalled を即「prompt失敗」と判断して同じ依頼を再送する。
- reviewer が存在しない場合に、無関係な Agent を勝手に reviewer
  として利用する。
- routing target を安全に解決できない場合に、別 agent name、role 単独名、保存済み pane ID、terminal ID 等へ自動 fallback する。
- `agent_session_id` や `terminal_id` を本来の用途から転用して独自の Agent routing identity として扱う。
- timeout / stalled 等の配送結果不明を無通知のまま放置し、人間を待機させ続ける。
- repo 固有レビュー規約を中央 Orchestrator に取り込む。
- approval / sandbox 機構を無効化することを導入要件にする。
- allowlist 対象の wrapper に任意 shell command 実行機能を持たせる。
- destructive operation を包括的な command prefix で自動許可する。

- Agent / repository Skill が Task Registry JSON を直接編集する。
- Herdr Agent lifecycle state を Orchestrator の task success と同一視する。
- Registry JSON に canonical schema と異なる flat key (`task_id` / `task_type` / `task_status` / `result_ref`) を混在させる。
- wrapper が `HERDR_ENV=1` 等の公式 Herdr 安全前提を迂回する。

---

<a id="sec-23"></a>

## 23. 今後の検証項目

- Cursor からの Skill 利用
- Codex からの Skill 利用
- Antigravity からの Skill 利用
- Agent ごとの Skill discovery 差異
- Cursor / Codex / Antigravity ごとの approval / allowlist 設定方法
- 固定 `herdr-orchestrator` CLI による承認回数の削減効果
- allowlist 候補 command の安全性レビュー
- reviewer が存在しない場合の標準動作
- sync wait の timeout 運用値
- stalled 時の復旧
- blocked 時の処理
- 複数 worker への並列 handoff
- UUIDv4 `task.id` の大量生成時の一意性・入力検証・path traversal拒否試験
- reviewer + tester の並列実行
- 複数結果の集約方法
- Agent role の動的起動を Orchestrator の責務に含めるか

---

- 未完了 task の一覧表示 (`task-list` 等) は MVP 必須要件とせず、Task Registry の運用実績を見て将来追加を検討する。

<a id="sec-24"></a>

## 24. 将来構成

MVP および専任 role 追加前は、テスト・調査依頼の worker 割当を [§4](02-roles-and-task-contract.md#sec-4) に統一する。以下は **将来** の目標図である。

将来的には `tester` / `researcher` 等の専任 role を追加し、以下のような複数 Agent orchestration を同一規約で扱えることを目標とする。

```text
                    ┌─ reviewer ────┐
                    │               │
requester ──────────┼─ tester ──────┼──► result aggregation
                    │               │
                    └─ researcher ──┘
```

ユーザーは Agent や pane
の詳細を意識せず、例えば以下のように依頼できる状態を目指す。

```text
レビュー依頼を出して
テストを依頼して
原因を調査して
reviewerとtesterに並列で確認させて
両方終わったら結果をまとめて
```

Herdr は Agent 間通信・実行基盤を担当し、`herdr-orchestrator` はその上に
共通の役割・委譲規約を提供する。
