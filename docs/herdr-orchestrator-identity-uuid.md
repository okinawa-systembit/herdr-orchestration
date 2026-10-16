# Herdr Orchestrator Repository Identity UUID化 設計書

- **対象 Issue**: [#50 — リモート origin 未設定のローカルリポジトリへの対応](https://github.com/okinawa-systembit/herdr-orchestration/issues/50)
- **ステータス**: 実装前設計案
- **関連ドキュメント**:
  - [02-roles-and-task-contract.md](herdr-orchestrator/02-roles-and-task-contract.md#sec-4) (§4: Agent 命名規則)
  - [06-results-and-workflows.md](herdr-orchestrator/06-results-and-workflows.md#sec-11-0) (§11.0: Worktree Root 解決と repository identity)
  - [07-skill-and-implementation.md](herdr-orchestrator/07-skill-and-implementation.md#sec-13) (§13: 実装境界)

---

## 1. 背景と目的

### 1.1 現状の課題

現在の Herdr Orchestrator は、Git の `origin` リモート URL から Repository Identity（canonical 文字列）を生成している。

この方式には以下の課題がある：

- `git init` 直後など、`origin` 未設定のリポジトリでは Repository Identity を生成できない。
- `origin` の URL 変更やリポジトリ名変更により、Repository Identity が変化する可能性がある。
- 日本語を含むディレクトリ名やローカル専用リポジトリを、安定した識別子で管理したい。
- 将来的な `git worktree` 利用を考慮する必要がある。

### 1.2 目的

Repository Identity の生成方式を、Git のリモート URL 依存から UUID v4 方式へ変更する。

これにより、次の状態を実現する：

1. `origin` 未設定の Git リポジトリでもオーケストレーションを利用できる。
2. `origin` の追加・変更・削除によって Repository Identity が変化しない。
3. リポジトリのディレクトリ名やパスに依存しない。
4. 人間による手動初期化と、依頼元エージェントによる自動初期化の両方に対応する。
5. 将来的な `git worktree` 利用時にも、同じ Git リポジトリで UUID を共有できる。
6. Git 未初期化ディレクトリでは処理を停止し、人間にチャットで報告する。

---

## 2. 基本設計方針

| 項目                     | 設計方針                                     |
| ------------------------ | -------------------------------------------- |
| Repository Identity      | UUID v4 に統一                               |
| UUID の必須性            | すべての Git リポジトリで必須                |
| UUID 生成タイミング      | 手動初期化時、または初回 handoff 時          |
| UUID 生成主体            | Orchestrator の共通関数                      |
| UUID 保存先              | Git リポジトリ共通のローカル config          |
| Git config キー          | `herdr.repository-id`                        |
| 既存 UUID                | 再利用し、変更しない                         |
| UUID 形式不正            | エラーとして停止                             |
| UUID 自動再生成          | 禁止                                         |
| origin URL               | Repository Identity の生成・照合に使用しない |
| ディレクトリ名           | UUID 生成には使用しない                      |
| Git 未初期化             | フォールバックせず停止                       |
| Git 未初期化時の報告     | 依頼元エージェントがチャットで報告           |
| Git 自動初期化           | 禁止                                         |
| 同時初期化               | 共通 Git ディレクトリ単位で排他制御          |
| git worktree             | 同一 Git リポジトリで UUID を共有            |
| 旧 origin 方式との互換性 | 維持しない                                   |

### 2.1 UUID の利用範囲

UUID は Git リポジトリ単位で発行する。

以下の単位では UUID を生成しない：

- エージェント単位
- タスク単位
- ブランチ単位
- worktree 単位
- ディレクトリ名単位

同じ Git リポジトリに属する複数の worktree は、同じ UUID を使用する。

### 2.2 UUID は認証情報ではない

UUID はリポジトリを識別するための値であり、認証やアクセス制御の代替ではない。

既存の Repository Identity、worktree path、Agent 名による照合・検証を維持する。

---

## 3. UUID の保存方式

### 3.1 保存先

Git ローカル config に保存する。

設定例：

```ini
[herdr]
    repository-id = 550e8400-e29b-41d4-a716-446655440000
```

設定値の確認例：

```bash
git config --local --get herdr.repository-id
```

UUID は `.git/config` に相当する共通リポジトリ設定へ保存する。

Git の通常のローカル設定を使用し、`git config --worktree` による worktree 固有設定には保存しない。

### 3.2 git worktree への対応

共通 Git ディレクトリを取得する。

```bash
git rev-parse --path-format=absolute --git-common-dir
```

UUID の保存先と排他制御は、共通 Git ディレクトリを基準とする。

例えば、次の構成では全 worktree で同じ UUID を使用する。

```text
my-app/
├── .git/
│   ├── config
│   ├── herdr-identity.lock
│   └── worktrees/
│       ├── my-app-dev/
│       └── my-app-fix/
│
worktrees/
├── my-app-dev/
└── my-app-fix/
```

> [!NOTE]
>
> - 上記は概念的なディレクトリ構成であり、実際の Git 管理ファイル配置は Git の構成によって異なる。
> - linked worktree では `.git` がディレクトリではなくファイルの場合がある。
> - UUID は worktree ごとに発行しない。
> - worktree 専用設定が有効でも、UUID は共通設定から明示的に取得する。
> - 同一リポジトリの別 worktree を識別する場合は、既存の worktree path 情報を使用する。

---

## 4. UUID 初期化機能

### 4.1 共通関数

次の 2 つの関数を用意する。

```python
get_or_create_repository_identity(worktree: Path) -> str
get_repository_identity(worktree: Path) -> str
```

#### `get_or_create_repository_identity(worktree)`

UUID の取得または初期化を担当する。

処理内容：

1. 対象ディレクトリが Git リポジトリに属しているか確認する。
2. Git 未初期化の場合は専用エラー（`git_repository_not_initialized`）を返す。
3. Git 共通ディレクトリを特定する（`git rev-parse --path-format=absolute --git-common-dir`）。
4. UUID 初期化用ロック（`herdr-identity.lock` 等）を取得する。
5. Git 共通 config から UUID を取得する。
6. UUID が存在する場合は形式を検証して返す。
7. UUID が存在しない場合は UUID v4 を生成する。
8. 生成した UUID を Git 共通 config に保存する。
9. 保存後に UUID を読み直して検証する。
10. ロックを解放して UUID を返す。

> [!IMPORTANT]
> UUID の保存に失敗した場合、生成した UUID を返してはならない。

#### `get_repository_identity(worktree)`

UUID の読み取り専用関数。

処理内容：

1. 対象ディレクトリが Git リポジトリに属しているか確認する。
2. Git 共通 config から UUID を取得する。
3. UUID の存在と形式を検証する。
4. 正常であれば UUID を返す。
5. 未登録または不正な場合はエラーを返す。

この関数では UUID の生成・変更・削除を行わない。

### 4.2 初期化処理の冪等性

`get_or_create_repository_identity()` は冪等とする。

同じリポジトリに対して何度実行しても、既存 UUID を変更しない。

初期化済みの場合は UUID を再生成しない。

### 4.3 排他制御

複数プロセスから初期化が同時に実行された場合でも、リポジトリに登録される UUID を 1 つに確定させる。

設計方針：

- Git 共通ディレクトリ単位でロックする。
- ロック取得後に UUID の存在を再確認する。
- UUID の生成・保存・読み直しをロックの保護範囲内で実行する。
- ロック解放は正常終了・異常終了の双方で保証する。
- ロック取得に失敗した場合、別 UUID を生成して処理を継続しない。
- ロックには Linux の `flock` 等、プロセス終了時に解放される OS の排他機構を使用する。

UUID v4 の偶発的な衝突検出を目的とした特別な仕組みは設けない。

### 4.4 UUID 形式の検証

UUID は RFC 4122 / RFC 9562 で定義される UUID v4 形式を採用する。

- UUID v4 であることを検証する。
- 不正な値を検出した場合は処理を停止する。
- 不正値を自動修復・自動再生成しない。
- エラーには原因と対処方法を含める。

---

## 5. CLI 仕様

以下の CLI コマンドを新設する（既存 CLI 構成と整合させる）。

### 5.1 `identity init`

```bash
herdr-orchestrator identity init
```

動作：

- Git リポジトリであることを確認する。
- UUID が未登録なら生成・保存する。
- UUID が登録済みなら再利用する。
- UUID が不正ならエラーにする。
- 正常終了時は確定した UUID を表示する。

人間による手動初期化に使用する。

エージェントによる自動初期化と同じ共通関数を呼び出す。

### 5.2 `identity show`

```bash
herdr-orchestrator identity show
```

動作：

- 登録済み UUID を表示する。
- UUID が未登録の場合はエラーにする。
- UUID の生成や変更は行わない。

### 5.3 CLI の実行対象

原則としてカレントディレクトリが属する Git リポジトリを対象とする。

将来的に worktree パスを明示指定できる拡張は許容するが、今回の必須要件には含めない。

---

## 6. handoff 時の動作

### 6.1 依頼元エージェント

依頼元エージェントが handoff を開始する場合、Repository Identity の取得・初期化を実行する。

処理フロー：

```text
handoff開始
    |
    v
Gitリポジトリ確認
    |
    +-- 未初期化
    |      |
    |      +-- エラー返却 (git_repository_not_initialized)
    |      +-- handoff中止
    |      +-- 人間へチャット報告
    |
    v
UUID取得・初期化 (get_or_create_repository_identity)
    |
    +-- UUID登録済み → 再利用
    |
    +-- UUID未登録 → UUID v4生成・保存
    |
    +-- UUID不正 → エラー終了
    |
    v
Repository Identity確定 (UUID v4)
    |
    v
Agent名生成 (UUIDベース)
    |
    v
宛先Agent照合
    |
    v
Task Registry登録
    |
    v
handoff実行
```

### 6.2 Agent 名の生成

Repository Identity として UUID v4 を使用する。
UUID（ハイフン含む 36 文字）は、それ自体が Herdr の Agent 名長上限（32 文字）を超えるため、短縮表現を用いた決定論的な名前生成が必要となる。

#### 6.2.1 Agent 名の衝突対策とハッシュ設計

UUID を短縮して Agent 名に埋め込む場合、単純に先頭 4 文字（`hash4`）など極端に短い切り出しを使用すると、複数リポジトリを同一マシン・同一 Herdr セッションで扱う環境において誕生日攻撃（Birthday Paradox）による衝突リスクが著しく高まる（16 進 4 桁 = 16 ビット = 65,536 通りであり、わずか数百リポジトリで衝突確率が急上昇する）。

そのため、Herdr の Agent 名制約を満たしつつ、実用上衝突しない十分なエントロピーを確保する設計を採用する。

##### 制約条件と文字数バジェット

1. **Herdr の agent name 制約**:
   - `[a-z][a-z0-9_-]{0,31}`（最大 32 文字、先頭英小文字）
2. **標準論理 role の長さ**:
   - `orchestrator`: 12 文字（最長）
   - `implementer`: 11 文字
   - `reviewer`: 8 文字
   - `design`: 6 文字
3. **区切り文字**:
   - `-` が 1〜2 箇所（1〜2 文字）
4. **ハッシュおよびプレフィックスのバジェット（最長 `orchestrator` 12 文字基準）**:
   - 最大長 32 文字から最長 role（12 文字）とハイフンを除くと、利用可能な最大文字数は **18〜19 文字**。

##### 命名規則の設計パターン

以下の 2 パターンを検討・検証する：

- **パターン A: 純粋ハッシュ型 (`r-<hash16>-<role>`)**
  - ディレクトリ名等の可変文字列に一切依存せず、UUID の SHA-256 から **16 進 16 文字（64 ビット）** を切り出す。
  - 計算例（最長 role `orchestrator`）:
    - `"r-"` (2) + `<hash16>` (16) + `"-"` (1) + `"orchestrator"` (12) = **31 文字**（<= 32 文字制約を完全遵守）。
  - **メリット**: $2^{64} \approx 1.8 \times 10^{19}$ 通りの極めて高いエントロピーを持ち、ハッシュ衝突は天文学的にゼロ。リポジトリのディレクトリ名や改名に全く左右されず完全決定論的。
  - **デメリット**: 人間が CLI や Herdr 画面で見た際に、どのプロジェクトのリポジトリか直感的に判別しにくい（`pane.label` での補完が必要）。
- **パターン B: スラッグ＋ハッシュ併用型 (`<slug>-<hash8>-<role>`)**
  - ディレクトリ名等の正規化スラッグ（最大 8〜10 文字）と、UUID の **16 進 8 文字（32 ビット）** ハッシュを組み合わせる。
  - 計算例（最長 role `orchestrator`）:
    - `<slug>` (最大 10 文字) + `"-"` (1) + `<hash8>` (8) + `"-"` (1) + `"orchestrator"` (12) = **32 文字**。
  - **メリット**: 人間がエージェント一覧を見た際にプロジェクト名が識別しやすい。
  - **メリット（衝突安全性）**: 32 ビットハッシュ（約 43 億通り）により、従来の `hash4`（16 ビット）と比べて衝突確率が $2^{16} = 65,536$ 倍安全化される。

> [!NOTE]
> 実装時は、既存の [§4.1](herdr-orchestrator/02-roles-and-task-contract.md#sec-4-1) の文字数切り詰めロジック（`prefix_len` 算出）をベースとしつつ、ハッシュ長を最低でも **8 文字（32 ビット）以上**（スラッグ省略時は最大 16 文字）確保することで、Agent 名の衝突リスクを決定論的に防止する。

#### 6.2.2 UUID 共有と worktree 照合・エージェント同時稼働の境界

本設計における「UUID 共有」と「worktree 照合」、および「将来の複数 worktree エージェント同時稼働」の境界を明確に分離して定義する。

1. **UUID 共有の範囲（本 Issue のスコープ）**:
   - 同一 Git リポジトリから派生したすべての worktree（メイン worktree および `git worktree add` で作成された linked worktree）は、共通 Git ディレクトリ経由で同一の `herdr.repository-id`（UUID）を参照・共有する。
   - これにより、同一リポジトリの異なる worktree であっても、リポジトリとしての同一論理コンテキストが保証される。
2. **worktree 照合による誤配送防止（既存安全弁の維持）**:
   - 複数 worktree で同じ UUID を共有していても、Orchestrator の worker 解決（[§11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0) / [§11.1](herdr-orchestrator/06-results-and-workflows.md#sec-11-1)）は、**`repository identity`（UUID）の一致確認**に加えて、**`worktree path`（チェックアウトパス）の完全一致**を必須条件として検証する。
   - したがって、同一リポジトリの別 worktree でエージェントが起動していたとしても、worktree パスが不一致であれば `worker_context_unverified` により安全に拒否され、タスクが誤配送・誤実行されることはない。
3. **複数 worktree での同 role エージェント同時稼働（本 Issue のスコープ外）**:
   - 本 Issue の命名規則では、Agent 名は `(UUID, role)`（またはスラッグ + UUID ハッシュ + role）から一意に決まるため、同一リポジトリの複数 worktree で同時に同じ role（例: worktree A の reviewer と worktree B の reviewer）を起動した場合、同じ `agent.name` となり Herdr のエージェント名前空間で衝突する。
   - 同一リポジトリの複数 worktree で同一 role のエージェントを真に並行稼働させるには、Agent 名に worktree 固有の識別子（worktree 名やパスハッシュ等）を組み込む命名規則拡張（例: `<slug>-<worktree-id>-<role>` 等）が必要となる。
   - **この Agent 名拡張は今回の Issue #50 ではスコープ外として明確に切り離す**（[§11](#11-対象外)）。
   - 本 Issue では「同一リポジトリ内では 1 role につき 1 エージェントの稼働」を基本運用前提とし、複数 worktree 間の安全な分離は既存の `worktree path` 照合によって厳格に担保する。

### 6.3 worker 側の動作

worker 側では UUID を自動生成しない。

既存 UUID を読み取り（`get_repository_identity`）、Task Registry に記録された Repository Identity と照合する。

検証対象：

- Repository Identity
- worktree path
- 必要に応じた既存の Agent 名・role 等のコンテキスト

不一致の場合は既存の安全停止ルールを適用する。

---

## 7. Git 未初期化ディレクトリの取り扱い

### 7.1 基本方針

Git リポジトリとして初期化されていないディレクトリでは、Orchestrator による handoff を禁止する。

以下のフォールバックは実装しない：

- ディレクトリ名からの Repository Identity 生成
- 絶対パスのハッシュによる Repository Identity 生成
- Git リポジトリ外への UUID 保存
- 一時的な UUID による handoff
- 自動的な `git init`

### 7.2 エラー仕様

Git 未初期化を識別する専用エラーコードを定義する。

```text
git_repository_not_initialized
```

Orchestrator はエラーコードと対処方法を返す。

依頼元エージェントはそのエラーを検知し、人間にチャットで報告する。

### 7.3 チャット報告例

```text
Herdrのタスク委託を開始できませんでした。

原因:
対象ディレクトリがGitリポジトリとして
初期化されていません。

対処方法:
対象ディレクトリで次のコマンドを実行してください。

  git init

Git初期化後、タスク委託を再実行してください。

Gitの初期化は自動実行していません。
```

### 7.4 責務の分離

- **Orchestrator 側**:
  - Git 未初期化の検出
  - 専用エラーの返却
  - UUID 生成の抑止
  - handoff の中止
  - 不完全な Task Registry 登録の防止
- **Skill・依頼元エージェント側**:
  - エラーの受信
  - 人間へのチャット報告
  - 原因と対処方法の提示
  - 人間による対処後の再試行

> [!CAUTION]
> エージェントがユーザーの承認なく `git init` を実行することは禁止する。

---

## 8. エラー処理

| 状態                       | 動作                         |
| -------------------------- | ---------------------------- |
| Git 未初期化               | 停止し、チャット報告         |
| UUID 未登録・初期化許可    | UUID 生成・保存              |
| UUID 未登録・読み取り専用  | エラー                       |
| UUID 形式不正              | 停止、自動修復禁止           |
| Git config 読み取り失敗    | エラー                       |
| Git config 書き込み失敗    | エラー、UUID を返さない      |
| ロック取得失敗             | エラー、別 UUID を生成しない |
| Repository Identity 不一致 | 既存の安全停止処理           |
| worktree path 不一致       | 既存の安全停止処理           |
| origin 未設定              | 正常動作                     |
| origin URL 変更            | UUID を維持                  |

すべてのエラーは、原因を識別できる形式で返す。

---

## 9. 既存環境からの移行

### 9.1 移行方針

現在は開発段階であるため、旧 origin URL 方式との後方互換性は維持しない。

既存の Repository Identity を UUID へ自動変換する処理も実装しない。

### 9.2 移行手順

1. 既存の Herdr エージェントを停止する。
2. 実行中タスクが存在しないことを確認する。
3. 必要な Task Registry をバックアップする。
4. 旧 Repository Identity を使用した Task Registry を整理する。
5. UUID 方式に対応した Orchestrator へ更新する。
6. 必要に応じて `identity init` で UUID を初期化する。
7. エージェントを再起動する。
8. 新しい Agent 名で handoff が成功することを確認する。

エージェントの再起動だけでは、旧 Task Registry に保存された Repository Identity は更新されないため、Registry の整理を移行作業に含める。

---

## 10. テスト計画

### 10.1 UUID 初期化

| ID    | テスト内容                   | 期待結果               |
| ----- | ---------------------------- | ---------------------- |
| ID-01 | `git init` 直後に初期化      | UUID 生成成功          |
| ID-02 | origin 設定済みで初期化      | UUID 生成成功          |
| ID-03 | 初期化済みリポジトリで再実行 | 同じ UUID              |
| ID-04 | CLI 手動初期化後に handoff   | UUID 再利用            |
| ID-05 | handoff による自動初期化     | UUID 生成成功          |
| ID-06 | 同時初期化                   | 同じ UUID に確定       |
| ID-07 | UUID 形式不正                | エラー、自動再生成なし |
| ID-08 | Git config 書き込み失敗      | エラー、handoff 中止   |

### 10.2 Git 環境

| ID     | テスト内容                     | 期待結果                   |
| ------ | ------------------------------ | -------------------------- |
| GIT-01 | Git 未初期化ディレクトリ       | handoff 拒否               |
| GIT-02 | Git 未初期化時のチャット報告   | 原因と対処方法を報告       |
| GIT-03 | Git 未初期化時の Task Registry | 不完全なタスクを作成しない |
| GIT-04 | origin 未設定                  | handoff 成功               |
| GIT-05 | origin URL 変更                | UUID 維持                  |
| GIT-06 | origin 削除                    | UUID 維持                  |
| GIT-07 | 日本語ディレクトリ名           | UUID 生成・handoff 成功    |
| GIT-08 | ディレクトリ移動・改名         | UUID 維持                  |

### 10.3 git worktree

| ID    | テスト内容                         | 期待結果           |
| ----- | ---------------------------------- | ------------------ |
| WT-01 | メイン worktree で UUID 生成       | 正常登録           |
| WT-02 | linked worktree から UUID 取得     | 同じ UUID          |
| WT-03 | linked worktree から初回 UUID 生成 | 共通 config に登録 |
| WT-04 | 複数 worktree から同時初期化       | 同じ UUID          |
| WT-05 | worktree path 不一致               | 安全停止           |
| WT-06 | worktree 専用設定有効              | 共通 UUID を利用   |

### 10.4 Agent・Task Registry

| ID    | テスト内容                                         | 期待結果              |
| ----- | -------------------------------------------------- | --------------------- |
| AG-01 | UUID から Agent 名生成                             | 決定論的な名前        |
| AG-02 | Agent 名最大長                                     | 32 文字以内           |
| AG-03 | 同じ UUID・role で再起動                           | 同じ Agent 名         |
| AG-04 | worker 側で UUID 未登録                            | 自動生成せずエラー    |
| AG-05 | Repository Identity 不一致                         | 安全停止              |
| AG-06 | 旧 origin 方式の Task Registry                     | 誤って引き継がない    |
| AG-07 | 異なる UUID での Agent 名分離 (8 文字以上ハッシュ) | 衝突せず異なる名前    |
| AG-08 | 同一 UUID かつ別 worktree path での worker 照合    | 安全停止 (誤配送抑止) |

---

## 11. 対象外

以下は本 Issue の実装対象外とする：

- Git 未初期化ディレクトリへの対応
- Git リポジトリの自動初期化
- UUID の自動再生成・自動修復
- 異なる Git リポジトリ間での UUID 共有
- リポジトリコピー時の UUID 重複を自動検出する仕組み
- 複数 PC 間での UUID 移行・共有機構
- 旧 origin URL 方式との互換性維持
- 旧 Task Registry の自動マイグレーション
- 複数 worktree で同じ role のエージェントを同時稼働させるための Agent 名拡張（worktree 固有識別子の Agent 名への組み込み）

※ 最後の項目は、将来的に `git worktree` を本格利用する際、別 Issue で検討する（[§6.2.2](#622-uuid-共有と-worktree-照合エージェント同時稼働の境界) 参照。UUID 共有自体は本 Issue で実装するが、同時稼働時の Agent 名拡張は明確に切り離す）。

---

## 12. 受け入れ条件

### Repository Identity

- [ ] 全 Git リポジトリで UUID v4 を Repository Identity として使用する。
- [ ] origin URL に依存しない。
- [ ] UUID を Git 共通 config に永続保存する。
- [ ] 既存 UUID を再利用する。
- [ ] UUID を自動再生成しない。
- [ ] UUID 形式不正時はエラーとする。

### 初期化

- [ ] 人間が CLI から UUID を初期化できる。
- [ ] 依頼元エージェントが初回 handoff 時に UUID を自動初期化できる。
- [ ] 手動・自動で同じ共通関数を使用する。
- [ ] 初期化処理が冪等である。
- [ ] 同時初期化で異なる UUID が確定しない。
- [ ] UUID 保存失敗時に handoff を開始しない。

### Git 未初期化

- [ ] Git 未初期化ディレクトリで handoff を拒否する。
- [ ] Git 未初期化時に UUID を生成しない。
- [ ] Git 未初期化時にフォールバックしない。
- [ ] `git init` を自動実行しない。
- [ ] 依頼元エージェントがチャットで人間に報告する。
- [ ] Git 未初期化時に不完全な Task Registry を作成しない。

### git worktree と安全照合

- [ ] 同一 Git リポジトリの複数 worktree で UUID を共有する。
- [ ] UUID 初期化の排他制御を共通 Git ディレクトリ単位で実施する。
- [ ] worktree path による既存の安全性検証を維持し、別 worktree への誤配送を防止する。
- [ ] 複数 worktree での同 role エージェント同時稼働拡張はスコープ外として切り離す。

### Agent 名と衝突対策

- [ ] UUID から Agent 名を決定論的に生成できる。
- [ ] 短縮ハッシュ長（最低 8 文字以上）を確保し、衝突リスクを排除する。
- [ ] Agent 名の 32 文字制限を満たす。
- [ ] worker 側は UUID を読み取り専用で扱う。
- [ ] Task Registry に UUID ベースの Repository Identity を記録する。
- [ ] 既存の handoff、result-submit、completion、recovery の動作を検証する。
- [ ] 設計書・Skill・CLI ヘルプ・テストを更新する。

---

## 13. 実装時の指示

本設計書を実装する際は、既存の Herdr Orchestrator の構成を確認し、既存関数・既存 CLI・Task Registry 仕様との整合性を維持すること。

特に次の点を確認する：

1. `identity.py` の既存 Repository Identity 生成処理。
2. `handoff_commands.py` での identity 取得と Agent 名生成。
3. `live_agent.py` での Repository Identity および worktree 照合。
4. Task Registry に保存される Repository Identity の形式。
5. `result-submit`、completion、recovery での identity 取得処理。
6. Skill 側での Git 未初期化エラーのチャット報告。
7. `git worktree` 環境での共通 Git config の参照方法。

> [!CAUTION]
> **【実装時の禁止事項】**
>
> - origin URL 方式への暗黙的フォールバック
> - パス名やディレクトリ名からの UUID 代替生成
> - worker 側での意図しない UUID 生成
> - UUID の自動上書き・自動再生成
> - Git 未初期化時の自動 `git init`
> - Repository Identity 不一致時の検証省略

既存仕様と衝突する箇所が見つかった場合は、独断で安全性検証を緩和せず、変更内容と影響範囲を報告すること。

---

## 14. 最終決定事項

本 Issue では、Repository Identity を Git リポジトリ単位の UUID v4 へ全面移行する。

UUID の初期化は、人間による CLI 実行と依頼元エージェントによる初回 handoff 時の自動生成の両方を許可する。

UUID の保存・取得・検証は Orchestrator の共通処理に集約する。

Git 未初期化ディレクトリでは一切のフォールバックを行わず、処理を中止して人間へチャットで報告する。

将来の `git worktree` 利用を考慮し、UUID は共通 Git リポジトリに保存する。

旧 origin URL 方式との後方互換性は維持しない。
