# 概要・設計原則・コンポーネント

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-1"></a>

## 1. 概要

<a id="sec-1-1"></a>

### 1.1 目的

Herdr を利用した AI Agent
間のタスク委譲・レビュー・結果返却を、複数リポジトリおよび複数 Agent
製品で共通利用できる仕組みとして整備する。

対象 Agent は以下とする。

- Cursor
- Codex
- Antigravity

本設計では、Herdr 公式 Skill
で提供されている機能と重複する独自実装を削減し、以下の原則で中央管理可能な
`herdr-orchestrator` を定義する。

> Herdr の操作方法は Herdr 公式 Skill を正とし、Orchestrator 側では Agent
> 間の役割・委譲・ワークフローのみを定義する。

<a id="sec-1-2"></a>

### 1.2 背景

個別リポジトリへ Herdr 固有処理を実装すると、以下の問題が発生する。

- リポジトリごとに Herdr 操作ロジックが重複する。
- Herdr CLI の仕様変更に各リポジトリが追従する必要がある。
- リポジトリ管理者が中央管理用 Skill の追加を許可しない場合がある。
- Cursor / Codex / Antigravity
  ごとの差異を各リポジトリが意識することになる。
- レビュー以外の tester / researcher 等へ拡張しにくい。

そのため、Herdr オーケストレーション機能をリポジトリ外で中央管理する。

---

<a id="sec-2"></a>

## 2. 設計原則

<a id="sec-2-1"></a>

### 2.1 公式機能優先

Herdr 公式 Skill で提供されている機能は、原則として独自実装しない。

Herdr CLI の以下の操作は公式 Skill の責務とする。

- Herdr 実行環境の判定
- pane / agent の取得
- pane の作成・分割
- Agent の起動
- Agent の状態取得
- Agent への prompt
- Agent の wait
- Agent 出力の read
- blocked / timeout / stalled 等の状態処理
- Herdr CLI の具体的な操作方法と lifecycle 処理

`herdr-orchestrator` 内へ Herdr CLI の操作仕様をコピーしない。CLI / server / Skill のバージョン差異は、[§18](08-adoption-and-verification.md#sec-18) の確認手順に従い、現在インストールされている CLI の `--help`、`api schema`、`--skill` および `herdr status` を確認して扱う。公式 Skill を自動的な互換レイヤーとはみなさない。

<a id="sec-2-2"></a>

### 2.2 リポジトリ非依存

中央 Skill は個別リポジトリへ配置しない。

```text
User Global Skills
├── herdr                 # Herdr公式
└── herdr-orchestrator

Repository A
└── repo固有Skillのみ

Repository B
└── repo固有Skillのみ
```

中央 Skill の利用のために以下を要求しない。

- repository への中央 Skill のコピー
- repository 内 symlink
- repository 固有設定ファイルの変更

<a id="sec-2-3"></a>

### 2.3 責務分離

システムを以下の3層に分離する。

```text
┌──────────────────────────────────────┐
│ Herdr公式 herdr Skill               │
│                                      │
│ Herdrを「どう操作するか」             │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ herdr-orchestrator                   │
│                                      │
│ Agentを「どう組み合わせるか」         │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ Repository固有 Skill                │
│                                      │
│ 「何を実行するか」                    │
└──────────────────────────────────────┘
```

<a id="sec-2-4"></a>

### 2.4 シンプル is ベスト（KISS 原則）

複雑な仕組み、過剰な状態管理、過剰なパーミッション縛り、必要以上の多段フォールバックを排除する。
最も単純で、壊れにくく、コード量が最小限で済むアプローチを最優先とする。

<a id="sec-2-5"></a>

### 2.5 人間中心の可観測性と可読性

AI エージェント専用のブラックボックスな構造を作らない。
人間の開発者がファイル一覧、ディレクトリ構成、ログ、コミット履歴、レビュー結果をエディタやターミナルで直感的に把握できるようにする。
作業領域は `.input/` に一元化し、隠しディレクトリを乱立させない。

<a id="sec-2-6"></a>

### 2.6 車輪の再発明の回避と既存エコシステムへの委譲

既存の標準ツール（`git`、`gh` CLI、Prettier、Unix 標準コマンド等）や公式機能で実現できることは、独自スクリプトで再実装せず素直に委譲・活用する。自前でパーサーや状態同期エンジンを作り込まず、既存の枯れたエコシステムに任せる。

<a id="sec-2-7"></a>

### 2.7 依存管理の境界分離（開発環境 vs 配布物）

依存関係の管理を以下の 2 つの境界で明確に分離する。

- **開発環境（本リポジトリ内）**: 開発効率・品質検証・整形の向上のため、有用なライブラリやツール（Prettier, markdownlint-cli2 等）を柔軟に導入・活用する。
- **配布物（中央 Skill・配布 CLI）**: 一般の開発者環境への導入障壁・依存トラブルをゼロにするため、Python 標準ライブラリ（組み込みモジュール・関数）および OS 標準コマンドのみで完結させる（外部依存ゼロ・高い可搬性の保証）。

<a id="sec-2-8"></a>

### 2.8 秘密の極小化（インライン渡し）

認証トークンや秘密鍵の存在範囲を極小化する。環境変数やファイルに広範に秘密をばらまかず、必要な 1 箇所だけにインラインで渡すことで、各スクリプトでの過剰な防御コードを削り、シンプルさを維持する。

<a id="sec-2-9"></a>

### 2.9 単一正本と自動検査のセット定義

ステータスや設定の多重管理を禁止する。正本を 1 つに限定し、人間向けの一覧は自動生成ビューとして提供するか、機械検査（lint/check スクリプト）でズレを検知する。

<a id="sec-2-10"></a>

### 2.10 機械検査とエージェント判断の境界分離

- **機械で止めるもの**: 秘密漏洩、宛先誤り、改ざん等、取り返しのつかない致命的事故の防止。固定形式（メタデータ行、マーカー等）の整合性。
- **エージェントに任せるもの**: 自由な文章の意味、妥当性、完成度、Markdown の見た目。
- **判断ルール**: 規則に「ただし〜を除く」が 2 つ以上必要なら機械チェックを作らない（パーサー化の禁止）。境界値を理由に 2 回修正された機械チェックは機能自体を削るか見直す。
- **テンプレート主導**: 機械が使う制御値はスクリプトが外側ヘッダーとして付与し、本文はエージェントが書く自由な Markdown としてスクリプトによる構文解析・自動挿入・過剰バリデーションを行わない。

---

<a id="sec-3"></a>

## 3. コンポーネント

<a id="sec-3-1"></a>

### 3.1 Herdr 公式 Skill

Herdr CLI を Agent から操作するための公式 Skill。

主な責務:

- `HERDR_ENV` 等の Herdr 環境情報の利用
- pane / agent の探索
- Agent の起動
- prompt
- wait
- get / read
- Agent lifecycle の処理
- timeout / stalled / blocked 等の安全な処理

Herdr CLI の具体的なコマンド選択については、本設計では再定義しない。導入中の `herdr --help`、`herdr api schema`、およびそのバージョンに同梱された `herdr --skill` を優先して確認する。

<a id="sec-3-2"></a>

### 3.2 herdr-orchestrator

共通の Agent オーケストレーション Skill。

主な責務:

- logical role と live agent name の規約
- repository / worktree affinity を含む worker routing
- `task.id` と Task Contract
- Task Registry の作成・更新・検索・lazy cleanup
- sync / async handoff の選択
- requester / worker 間の関係管理
- Result Contract
- `completion.action` と completion state / recovery
- Agent が存在しない場合の方針
- 複数 Agent を利用するワークフローの共通規約
- 承認頻度を抑える安定した command surface の提供

Herdr の状態モデル、CLI の意味、利用可能な操作は Herdr 公式 Skill / CLI を正とする。`herdr-orchestrator` の script / CLI は、それらを再定義するのではなく、定型処理・原子的状態更新・承認対象コマンドの安定化のために利用する。

<a id="sec-3-3"></a>

### 3.3 Repository 固有 Skill

プロジェクト固有の処理を担当する。

レビューの場合は以下が該当する。

- Review Scope
- lint / format / build
- Review Prompt
- RNNN
- INDEX
- Finding ID
- Follow-up 判定
- Review Status
- Review Response
- プロジェクト固有のセキュリティチェック

これらを `herdr-orchestrator` へ移動しない。

---
