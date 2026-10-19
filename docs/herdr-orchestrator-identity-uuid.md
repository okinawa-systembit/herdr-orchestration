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

> [!NOTE]
> Issue #50 の起票段階では、`origin` 未設定時にディレクトリ名やローカルパスを利用するフォールバック案（例: `local/<directory_name>`）も検討された。
> しかし、ディレクトリ改名や移動に脆弱であること、日本語・非 ASCII パスの扱い、同一マシン内での重複リスクなどの課題があるため、本設計ではフォールバック案を採用せず、Git 共通 config に UUID v4 を保存・管理する恒久方式へ全面的に置き換える。

### 1.2 目的

Repository Identity の生成方式を、Git のリモート URL 依存から UUID v4 方式へ変更する（Issue #50 の旧フォールバック案を本 UUID 方式に置き換える）。

これにより、次の状態を実現する：

1. `origin` 未設定の Git リポジトリでもオーケストレーションを利用できる。
2. `origin` の追加・変更・削除によって Repository Identity が変化しない。
3. リポジトリのディレクトリ名やパスに依存しない。
4. 人間による手動初期化と、依頼元エージェントによる自動初期化の両方に対応する。
5. 将来的な `git worktree` 利用時にも、同じ Git リポジトリで UUID を共有できる。
6. Git 未初期化ディレクトリでは処理を停止し、人間にチャットで報告する。

---

## 2. 基本設計方針

| 項目                     | 設計方針                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------ |
| Repository Identity      | UUID v4 に統一                                                                       |
| UUID の必須性            | すべての Git リポジトリで必須                                                        |
| UUID 生成タイミング      | 手動初期化時、または初回 handoff 時                                                  |
| UUID 生成主体            | Orchestrator の共通関数                                                              |
| UUID 保存先              | 共通 Git ディレクトリ配下の `config` ファイル（`<git-common-dir>/config`）           |
| Git config キー          | `herdr.repository-id`                                                                |
| 既存 UUID                | 再利用し、変更しない                                                                 |
| UUID 形式不正            | エラーとして停止                                                                     |
| UUID 自動再生成          | 禁止                                                                                 |
| 保存失敗時の動作         | handoff を中止。次回実行時は有効な保存済み UUID があれば再利用し、安易に再生成しない |
| origin URL               | Repository Identity の生成・照合に使用しない                                         |
| ディレクトリ名           | UUID 生成・Agent 名生成に使用しない                                                  |
| Agent 命名規則           | パターン A `r-<hash16>-<role>` に正式確定（パス・ディレクトリ名・URL 完全非依存）    |
| Git 未初期化             | フォールバックせず停止（CLI 専用エラー `git_repository_not_initialized` 返却）       |
| Git 未初期化時の報告     | 依頼元エージェント / Skill がチャットで報告                                          |
| Git 自動初期化           | 禁止                                                                                 |
| 同時初期化               | 共通 Git ディレクトリ単位で排他制御                                                  |
| git worktree             | 同一 Git リポジトリ（共通 Git ディレクトリ）で UUID を共有                           |
| `config.worktree` の影響 | 共通 config ファイルを明示指定して読み書きするため、影響を受けない                   |
| 旧 origin 方式との互換性 | 維持しない                                                                           |

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

共通 Git ディレクトリ配下のローカル `config` ファイルに明示的に保存する。

設定例（`<git-common-dir>/config`）：

```ini
[herdr]
    repository-id = 550e8400-e29b-41d4-a716-446655440000
```

UUID は共通 Git ディレクトリ配下の `config`（例: `.git/config`）に直接読み書きする。
通常の `git config --local` では `extensions.worktreeConfig` が有効な linked worktree において `config.worktree` へ書き込まれる可能性があるため、Orchestrator では共通 Git ディレクトリを解決し、ファイルパスを明示したコマンドを使用する。

設定値の確認例：

```bash
git config --file "$(git rev-parse --path-format=absolute --git-common-dir)/config" --get herdr.repository-id
```

設定値の書き込み例：

```bash
git config --file "$(git rev-parse --path-format=absolute --git-common-dir)/config" herdr.repository-id 550e8400-e29b-41d4-a716-446655440000
```

### 3.2 git worktree への対応

共通 Git ディレクトリを取得する。

```bash
git rev-parse --path-format=absolute --git-common-dir
```

UUID の保存先と排他制御は、共通 Git ディレクトリを基準とする。

例えば、次の構成では全 worktree で共通の `config` ファイルおよび同じ UUID を使用する。

```text
my-app/
├── .git/
│   ├── config                  <-- UUID 保存先（全 worktree で共有）
│   ├── herdr-identity.lock     <-- 排他制御ロックファイル
│   └── worktrees/
│       ├── my-app-dev/
│       │   └── config.worktree <-- 仮に存在しても UUID 取得・設定に影響しない
│       └── my-app-fix/
│
worktrees/
├── my-app-dev/
└── my-app-fix/
```

> [!NOTE]
>
> - 上記は概念的なディレクトリ構成であり、実際の Git 管理ファイル配置は Git の構成によって異なる。
> - linked worktree では `.git` がディレクトリではなくファイル（メイン Git ディレクトリへのポインタ）である。
> - UUID は worktree ごとに発行しない。
> - `extensions.worktreeConfig` が有効で `config.worktree` に同名キーが存在しても、共通 Git ディレクトリ配下の `config` を明示指定して読み書きするため、UUID の取得・設定結果に影響を与えない。
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
2. Git 未初期化の場合は専用エラー（`git_repository_not_initialized`）を返し、処理を停止する（CLI 責務。Skill 側チャット報告の契機）。
3. 共通 Git ディレクトリを特定する（`git rev-parse --path-format=absolute --git-common-dir`）。
4. UUID 初期化用排他ロック（`<git-common-dir>/herdr-identity.lock` 等）を取得する。
5. 共通 Git config（`git config --file <git-common-dir>/config --get herdr.repository-id`）から UUID を取得する（`config.worktree` は参照しない）。
6. UUID が既に存在する場合は形式を検証し、正常であればロックを解放してその UUID を返す（既存 UUID の再利用）。
7. UUID が存在しない場合は、UUID v4 を生成する。
8. 生成した UUID を共通 Git config に保存する（`git config --file <git-common-dir>/config herdr.repository-id <uuid>`）。
9. 保存後に共通 Git config から UUID を読み直して検証する。
10. 保存または読み直し検証に失敗した場合（ディスク容量不足、アクセス権限エラー、値の不整合等）、生成した UUID を返さずに handoff を中止し、エラーを発生させる。
11. 次回実行時は、共通 Git config の保存済み UUID を再確認し、有効な UUID が存在する場合はそれを再利用する。前回の失敗を理由として安易に新規 UUID を再生成・上書きしない。
12. ロックを解放して確定した UUID を返す。

> [!IMPORTANT]
>
> - UUID の保存または読み直し検証に失敗した場合、未保存または不整合の可能性がある UUID を返してはならず、handoff を中止しなければならない。
> - 再実行時は保存済み UUID の有無を必ず再確認し、有効な UUID が存在する場合はそれを再利用する。前回の失敗を理由とした新規 UUID の無制限な再生成は禁止する。

#### `get_repository_identity(worktree)`

UUID の読み取り専用関数。

処理内容：

1. 対象ディレクトリが Git リポジトリに属しているか確認する（未初期化の場合は `git_repository_not_initialized` エラー）。
2. 共通 Git ディレクトリを取得し、共通 Git config（`git config --file <git-common-dir>/config --get herdr.repository-id`）から UUID を取得する（`config.worktree` に影響されない）。
3. UUID の存在と形式を検証する。
4. 正常であれば UUID を返す。
5. 未登録または不正な場合はエラーを返す。

この関数では UUID の生成・変更・削除を行わない。

### 4.2 初期化処理の冪等性と再試行安全性

`get_or_create_repository_identity()` は冪等とする。

同じリポジトリに対して何度実行しても、既存 UUID を変更しない。

初期化済みの場合は UUID を再生成しない。

保存や読み直しに失敗した後に再実行された場合でも、共通 Git config の状態を再確認し、有効な UUID が保存されていればそれを再利用する。失敗状態を契機とする新規 UUID の自動再生成や上書きは行わない。

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
    |      +-- CLI専用エラー返却 (git_repository_not_initialized)
    |      +-- handoff中止
    |      +-- 人間へチャット報告 (Skill責務)
    |
    v
UUID取得・初期化 (get_or_create_repository_identity)
    |
    +-- UUID登録済み → 再利用
    |
    +-- UUID未登録 → UUID v4生成・保存
    |      |
    |      +-- 保存・検証失敗 → handoff中止 (次回再確認で再利用)
    |
    +-- UUID不正 → エラー終了
    |
    v
Repository Identity確定 (UUID v4)
    |
    v
Agent名生成 (パターンA: r-<hash16>-<role>)
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

#### 6.2.1 Agent 名生成規則の確定（パターン A: `r-<hash16>-<role>`）

Herdr の Agent 名制約を満たし、かつディレクトリ名・worktree パス・origin URL に一切依存しない決定論的な命名規則として、**パターン A（純粋ハッシュ型）を正式採用する**。

##### 正式仕様

```text
r-<hash16>-<role>
```

- **プレフィックス (`r-`)**:
  - Herdr の agent name 制約（`[a-z]` で始まる）を満たす固定プレフィックス（2 文字）。
- **ハッシュ値 (`<hash16>`)**:
  - 正規化された UUID 文字列（RFC 4122 / RFC 9562 準拠、英小文字・ハイフン区切りの 36 文字、例: `550e8400-e29b-41d4-a716-446655440000`）の SHA-256 ダイジェスト先頭 16 文字（16 進英小文字、64 ビット）。
- **区切り文字 (`-`)**:
  - ハッシュと role を接続するハイフン（1 文字）。
- **論理 role (`<role>`)**:
  - Orchestrator の標準論理 role 名（`orchestrator`, `implementer`, `reviewer`, `design` 等）。

##### 制約条件と文字数バジェットの検証

1. **Herdr の agent name 制約**:
   - `[a-z][a-z0-9_-]{0,31}`（最大 32 文字、先頭英小文字）
2. **文字数計算**:
   - 最長 role `orchestrator`（12 文字）:
     `"r-"` (2) + `<hash16>` (16) + `"-"` (1) + `"orchestrator"` (12) = **31 文字**（<= 32 文字制約を完全遵守）。
   - `implementer` (11 文字): 30 文字
   - `reviewer` (8 文字): 27 文字
   - `design` (6 文字): 25 文字
3. **完全非依存性**:
   - ディレクトリ名、worktree path、origin URL に Agent 名を一切依存させない。
   - リポジトリの配置場所の変更、ディレクトリ改名、リモート URL の変更・削除があっても、Agent 名は常に一意かつ不変に保たれる。

##### ハッシュ衝突確率の評価

- ハッシュ空間は 64 ビット（$2^{64} \approx 1.84 \times 10^{19}$ 通り）である。
- 同一マシン・同一 Herdr セッションで稼働するリポジトリ数が数千リポジトリ規模に達した場合でも、誕生日攻撃（Birthday Paradox）に基づく衝突確率は $p \approx \frac{n^2}{2 \times 2^{64}} \ll 10^{-11}$ であり、実用上極めて無視できる水準に抑えられる（数学的にゼロではないが、運用上衝突リスクは排除される）。

##### 検討パターン B（スラッグ併用型）の不採用理由

- 設計初期に検討されたパターン B（`<slug>-<hash8>-<role>`: ディレクトリ名スラッグと 8 文字ハッシュの併用）は、以下の理由により**不採用**とする：
  1. ディレクトリ改名やパス変更によってスラッグが変わり、Agent 名の不変性が損なわれる。
  2. 日本語ディレクトリ名や長大なディレクトリ名に対するサニタイズ処理が複雑化する。
  3. スラッグに文字数を割くことでハッシュが 32 ビット（約 43 億通り）に短縮され、衝突余裕度が低下する。
  4. 人間可読性は Herdr の `pane.label`（リポジトリ名や worktree パスを表示）で十分に補完可能である。

#### 6.2.2 UUID 共有と worktree 照合・エージェント同時稼働の境界

本設計における「UUID 共有」と「worktree 照合」、および「将来の複数 worktree エージェント同時稼働」の境界を明確に分離して定義する。

1. **UUID 共有の範囲（本 Issue のスコープ）**:
   - 同一 Git リポジトリから派生したすべての worktree（メイン worktree および `git worktree add` で作成された linked worktree）は、共通 Git ディレクトリ経由で同一の `herdr.repository-id`（UUID）を参照・共有する。
   - これにより、同一リポジトリの異なる worktree であっても、リポジトリとしての同一論理コンテキストが保証される。
2. **worktree 照合による誤配送防止（既存安全弁の維持）**:
   - 複数 worktree で同じ UUID を共有していても、Orchestrator の worker 解決（[§11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0) / [§11.1](herdr-orchestrator/06-results-and-workflows.md#sec-11-1)）は、**`repository identity`（UUID）の一致確認**に加えて、**`worktree path`（チェックアウトパス）の完全一致**を必須条件として検証する。
   - したがって、同一リポジトリの別 worktree でエージェントが起動していたとしても、worktree パスが不一致であれば `worker_context_unverified` により安全に拒否され、タスクが誤配送・誤実行されることはない。
3. **複数 worktree での同 role エージェント同時稼働（本 Issue のスコープ外）**:
   - パターン A の命名規則では、Agent 名は `(UUID, role)` から一意に決まるため、同一リポジトリの複数 worktree で同時に同じ role（例: worktree A の reviewer と worktree B の reviewer）を起動した場合、同じ `agent.name` となり Herdr のエージェント名前空間で衝突する。
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

Issue #50 起票時に検討された旧フォールバック案（ディレクトリ名による識別）は、本設計の Git 共通 config への UUID 保存方式によって完全に置き換えられた。したがって、Git 未初期化ディレクトリに対する代替フォールバックは一切実装しない：

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

- **CLI 側の動作**:
  - 対象ディレクトリが Git 管理下でないことを検出した場合、専用エラーコード `git_repository_not_initialized` とエラーメッセージを出力し、非ゼロの終了コードで即座に処理を停止する。
  - Task Registry への不完全なタスク登録や、一時 UUID の発行は行わない。
- **Skill 側の動作**:
  - CLI から `git_repository_not_initialized` を受信した場合、タスク委託処理を中止し、人間にチャットで原因と対処方法を明確に報告する。

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

CLI（Orchestrator コア）と Skill（エージェント側対話インターフェース）の責務を厳格に分離する：

- **Orchestrator CLI 側**:
  - Git 未初期化の検出
  - 専用エラーコード（`git_repository_not_initialized`）の返却と非ゼロ終了
  - UUID 生成の抑止
  - handoff コマンドの即時中止
  - 不完全な Task Registry 登録の防止
- **Skill・依頼元エージェント側**:
  - CLI エラーの受信と判定
  - 人間へのチャット報告（チャット UI へのメッセージ出力）
  - 原因（Git 未初期化）と対処方法（`git init`）の提示
  - `git init` を自動実行しない方針の維持
  - 人間による対処後の再試行支援

> [!CAUTION]
> エージェントがユーザーの承認なく `git init` を実行することは禁止する。

---

## 8. エラー処理

| 状態                              | 動作                                                                                                              |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Git 未初期化                      | CLI: `git_repository_not_initialized` エラーで停止。Skill: 人間へチャット報告                                     |
| UUID 未登録・初期化許可           | UUID 生成・共通 Git config 保存・読み直し検証                                                                     |
| UUID 未登録・読み取り専用         | エラー、UUID 生成せず安全停止                                                                                     |
| UUID 形式不正                     | 停止、自動修復・自動再生成禁止                                                                                    |
| Git config 読み取り失敗           | エラー                                                                                                            |
| Git config 書き込み・読み直し失敗 | handoff 中止、UUID を返さない。次回実行時は保存済み UUID を再確認し、有効なら再利用（失敗を理由とする再生成禁止） |
| ロック取得失敗                    | エラー、別 UUID を生成しない                                                                                      |
| `config.worktree` に同名キー存在  | 共通 config（`<git-common-dir>/config`）を明示指定して読み書きするため影響なし（共通 UUID を正しく維持）          |
| Repository Identity 不一致        | 既存の安全停止処理                                                                                                |
| worktree path 不一致              | 既存の安全停止処理                                                                                                |
| origin 未設定                     | 正常動作（UUID を利用）                                                                                           |
| origin URL 変更・削除             | 正常動作（UUID を維持）                                                                                           |

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

| ID    | テスト内容                        | 期待結果                                                         |
| ----- | --------------------------------- | ---------------------------------------------------------------- |
| ID-01 | `git init` 直後に初期化           | UUID 生成成功                                                    |
| ID-02 | origin 設定済みで初期化           | UUID 生成成功                                                    |
| ID-03 | 初期化済みリポジトリで再実行      | 同じ UUID（既存 UUID 再利用）                                    |
| ID-04 | CLI 手動初期化後に handoff        | UUID 再利用                                                      |
| ID-05 | handoff による自動初期化          | UUID 生成成功                                                    |
| ID-06 | 同時初期化                        | 同じ UUID に確定（排他制御）                                     |
| ID-07 | UUID 形式不正                     | エラー、自動再生成なし                                           |
| ID-08 | Git config 書き込み・読み直し失敗 | エラー返却、handoff 中止（次回実行時に有効 UUID があれば再利用） |

### 10.2 Git 環境

| ID     | テスト内容                                | 期待結果                                                                |
| ------ | ----------------------------------------- | ----------------------------------------------------------------------- |
| GIT-01 | Git 未初期化時の CLI 動作（CLI 責務）     | `git_repository_not_initialized` 専用エラー返却、非ゼロ終了コードで停止 |
| GIT-02 | Git 未初期化時の Skill 動作（Skill 責務） | `git init` を自動実行せず、チャットで原因と対処方法を人間へ報告         |
| GIT-03 | Git 未初期化時の Task Registry            | 不完全なタスクを作成しない                                              |
| GIT-04 | origin 未設定                             | handoff 成功                                                            |
| GIT-05 | origin URL 変更                           | UUID 維持                                                               |
| GIT-06 | origin 削除                               | UUID 維持                                                               |
| GIT-07 | 日本語ディレクトリ名                      | UUID 生成・handoff 成功                                                 |
| GIT-08 | ディレクトリ移動・改名                    | UUID 維持                                                               |

### 10.3 git worktree

| ID    | テスト内容                         | 期待結果                                                     |
| ----- | ---------------------------------- | ------------------------------------------------------------ |
| WT-01 | メイン worktree で UUID 生成       | 正常登録                                                     |
| WT-02 | linked worktree から UUID 取得     | 同じ UUID                                                    |
| WT-03 | linked worktree から初回 UUID 生成 | 共通 Git config（`<git-common-dir>/config`）に登録           |
| WT-04 | 複数 worktree から同時初期化       | 同じ UUID（共通ロック排他制御）                              |
| WT-05 | worktree path 不一致               | 安全停止（`worker_context_unverified`）                      |
| WT-06 | `config.worktree` に同名キー存在   | 共通 config（`<git-common-dir>/config`）の UUID を確実に取得 |

### 10.4 Agent・Task Registry

| ID    | テスト内容                                           | 期待結果                                                                              |
| ----- | ---------------------------------------------------- | ------------------------------------------------------------------------------------- |
| AG-01 | パターン A による Agent 名生成 (`r-<hash16>-<role>`) | 決定論的な名前（小文字・ハイフン接続）                                                |
| AG-02 | Agent 名最大長検証（最長 `orchestrator`）            | 31 文字（<= 32 文字制約を完全遵守）                                                   |
| AG-03 | 同じ UUID・role で再起動                             | 同じ Agent 名                                                                         |
| AG-04 | worker 側で UUID 未登録                              | 自動生成せずエラー停止                                                                |
| AG-05 | Repository Identity 不一致                           | 安全停止                                                                              |
| AG-06 | 旧 origin 方式の Task Registry                       | 誤って引き継がない                                                                    |
| AG-07 | 異なる UUID での Agent 名分離                        | 異なる UUID を用いたテストケースで、期待する異なる Agent 名が生成されることを確認する |
| AG-08 | ディレクトリ名改名・パス変更時の Agent 名            | Agent 名が一切変化しない（パス・ディレクトリ完全非依存）                              |
| AG-09 | 同一 UUID かつ別 worktree path での worker 照合      | 安全停止（誤配送抑止）                                                                |

---

## 11. 対象外

以下は本 Issue の実装対象外とする：

- Issue #50 初期に検討されたディレクトリ名等のローカル識別子フォールバック案（本 UUID 方式に全面置き換えのため廃止）
- Git 未初期化ディレクトリへの対応（手動 `git init` を必須とする）
- Git リポジトリの自動初期化（`git init` の自動実行禁止）
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
- [ ] origin URL やディレクトリ名に依存しない。
- [ ] Issue #50 の旧フォールバック案を廃止し、本 UUID 方式に完全に置き換える。
- [ ] UUID を共通 Git ディレクトリ配下の `config`（`<git-common-dir>/config`）に永続保存する。
- [ ] 既存 UUID を再利用する。
- [ ] UUID を自動再生成しない。
- [ ] UUID 形式不正時はエラーとする。

### 初期化と保存失敗対策

- [ ] 人間が CLI から UUID を初期化できる（`herdr-orchestrator identity init`）。
- [ ] 依頼元エージェントが初回 handoff 時に UUID を自動初期化できる。
- [ ] 手動・自動で同じ共通関数を使用する。
- [ ] 初期化処理が冪等である。
- [ ] 同時初期化で異なる UUID が確定しない。
- [ ] UUID 保存・読み直し失敗時に handoff を中止し、UUID を返さない。
- [ ] 次回実行時は保存済み UUID を再確認し、有効な UUID が存在する場合は再利用する（失敗を理由に再生成しない）。

### Git 未初期化と責務分離

- [ ] Git 未初期化ディレクトリで handoff を拒否する。
- [ ] Git 未初期化時に UUID を生成しない。
- [ ] Git 未初期化時にフォールバックしない。
- [ ] `git init` を自動実行しない。
- [ ] CLI は専用エラー `git_repository_not_initialized` と非ゼロ終了コードで停止する（CLI 責務の単体検証）。
- [ ] 依頼元エージェント / Skill がエラーを検知してチャットで人間に報告する（Skill 責務の検証）。
- [ ] Git 未初期化時に不完全な Task Registry を作成しない。

### git worktree と安全照合

- [ ] 同一 Git リポジトリの複数 worktree で UUID を共有する。
- [ ] UUID 初期化の排他制御を共通 Git ディレクトリ単位で実施する。
- [ ] `config.worktree` に同名キーが存在しても、共通 config の UUID を取得・維持し、影響を受けない。
- [ ] worktree path による既存の安全性検証を維持し、別 worktree への誤配送を防止する。
- [ ] 複数 worktree での同 role エージェント同時稼働拡張はスコープ外として切り離す。

### Agent 名と衝突対策

- [ ] パターン A（`r-<hash16>-<role>`）を正式採用し、UUID から Agent 名を決定論的に生成する。
- [ ] パターン B（スラッグ併用型）は不採用とする。
- [ ] `<hash16>` は正規化された UUID の SHA-256 ダイジェスト先頭 16 桁（16 進小文字）とする。
- [ ] ディレクトリ名、worktree path、origin URL に Agent 名を一切依存させない。
- [ ] Agent 名の 32 文字制限を満たす（最長 `orchestrator` で 31 文字）。
- [ ] ハッシュ衝突確率をゼロと断定せず、確率論的（実用上極めて無視できる水準）に記述する。
- [ ] worker 側は UUID を読み取り専用で扱う。
- [ ] Task Registry に UUID ベースの Repository Identity を記録する。
- [ ] 既存の handoff、result-submit、completion、recovery の動作を検証する。
- [ ] 設計書・Skill・CLI ヘルプ・テストを更新する。

### インストーラーの更新安全性（PR52-01）

- [ ] 開発用インストーラー（`dev-local-install.sh`, `setup.sh`）において、既存ディレクトリの多重ネスト（`scripts/scripts` 等）を防止する。
- [ ] `--dest` にルートディレクトリ（`/`）やホームディレクトリ直下（`$HOME`）などの危険なパスが指定された場合に安全に拒絶する。
- [ ] コピー途中の失敗時でも既存の正常なインストール環境が破壊されない失敗耐性（ステージング展開またはロールバック保護）を担保する。

---

## 13. 実装時の指示

本設計書を実装する際は、既存の Herdr Orchestrator の構成を確認し、既存関数・既存 CLI・Task Registry 仕様との整合性を維持すること。

特に次の点を確認する：

1. `identity.py` の既存 Repository Identity 生成処理。
2. `handoff_commands.py` での identity 取得と Agent 名生成（パターン A 適用）。
3. `live_agent.py` での Repository Identity および worktree 照合。
4. Task Registry に保存される Repository Identity の形式。
5. `result-submit`、completion、recovery での identity 取得処理。
6. Skill 側での Git 未初期化エラーのチャット報告。
7. `git worktree` 環境での共通 Git config（`<git-common-dir>/config`）の参照方法。
8. インストーラー（`dev-local-install.sh`, `setup.sh`）における多重ネスト防止と安全な置換方式（[§15](#15-インストーラーによる多重ネスト防止と安全な置換設計pr52-01-対応) 参照）。

> [!CAUTION]
> **【実装時の禁止事項】**
>
> - origin URL 方式やディレクトリ名方式への暗黙的フォールバック
> - パス名やディレクトリ名からの UUID 代替生成
> - worker 側での意図しない UUID 生成
> - UUID の自動上書き・自動再生成
> - Git 未初期化時の自動 `git init`
> - Repository Identity 不一致時の検証省略
> - インストール先ディレクトリの無条件な直接削除（Fail-Closed ではない直接 `rm -rf`）

既存仕様と衝突する箇所が見つかった場合は、独断で安全性検証を緩和せず、変更内容と影響範囲を報告すること。

---

## 14. 最終決定事項

1. **Repository Identity の UUID v4 移行**:
   全 Git リポジトリで UUID v4 を Repository Identity として使用する。Issue #50 起票時の旧フォールバック案（ディレクトリ名による識別）は廃止し、本 UUID 方式に完全に置き換える。
2. **保存先の共通 Git config 固定**:
   UUID の保存・取得先を共通 Git ディレクトリ配下の `config` ファイル（`<git-common-dir>/config`）に明示的に固定する。これにより、`extensions.worktreeConfig` 等による `config.worktree` の影響を完全に排除する。
3. **Agent 命名規則の確定（パターン A）**:
   パターン A `r-<hash16>-<role>` を正式採用する。`<hash16>` は正規化された UUID の SHA-256 ダイジェスト先頭 16 桁（64 ビット）とし、最長 role（`orchestrator`）でも 31 文字で 32 文字制約を満たす。ディレクトリ名・パス・origin URL に一切依存させず、衝突確率は実用上極めて無視できる水準とする。パターン B は不採用とする。
4. **保存失敗時のフェイルセーフと再試行時の再利用**:
   UUID 保存・読み直し検証失敗時は handoff を中止し、UUID を返さない。次回実行時は共通 Git config の状態を再確認し、有効な保存済み UUID が存在する場合は再利用し、安易に再生成しない。
5. **Git 未初期化時の責務分離**:
   Git 未初期化ディレクトリではフォールバックせず停止する。Orchestrator CLI は専用エラー `git_repository_not_initialized` と非ゼロ終了コードで停止し、Skill / 依頼元エージェントがチャットで人間へ原因と `git init` 対処法を報告する。自動 `git init` は禁止する。
6. **後方互換性**:
   旧 origin URL 方式との後方互換性は維持しない。移行手順に従い Task Registry の整理を実施する。
7. **複数 worktree エージェント同時稼働拡張の分離**:
   同一リポジトリの複数 worktree 間での UUID 共有と `worktree path` 安全照合を本 Issue で実装し、同 role エージェントの同時稼働用 Agent 名拡張は将来の別 Issue として切り離す。
8. **インストーラーの更新安全性と多重ネスト防止（PR52-01 対応）**:
   開発用インストーラーにおける既存ディレクトリの多重ネスト防止は必須とするが、直接の `rm -rf` による事前削除ではなく、`--dest` の危険パス検証とステージング展開／バックアップ退避による失敗耐性を備えた安全な置換方式を採用する。コピー失敗時に既存の健全なインストール環境を破壊しないことを保証する。

---

## 15. インストーラーによる多重ネスト防止と安全な置換設計（PR52-01 対応）

### 15.1 背景と課題

開発用インストーラー（`skills/herdr-orchestrator/install/dev-local-install.sh`）および Cursor レガシー用（`skills/herdr-orchestrator/install/setup.sh`）は、リポジトリ正本（`skills/herdr-orchestrator/`）からユーザーグローバル領域（`~/.agents/skills/herdr-orchestration/` 等）へ Skill ツリー（`SKILL.md`, `scripts/`, `references/`）をコピーする。

従来の `cp -a "$src" "$dst"` では、配置先に既にディレクトリが存在する場合に OS の `cp` の仕様により配下に多重ネスト（例: `scripts/scripts/`）が発生する問題があった。

この多重ネストを防止するため、直前で `rm -rf "$dst"` を行ってからコピーする処理が導入されたが、コードレビュー（PR52-01）において以下のリスクが指摘された：

1. **既存インストールの即時喪失**: コピー開始前に既存環境が削除されるため、コピー途中でエラー（ディスク容量不足、パーミッションエラー、シグナル中断等）が発生した場合に、削除前の正常なインストール環境が失われ復旧できなくなる。
2. **危険なパス指定のリスク**: `--dest` オプションに誤ってルート（`/`）やホーム直下（`$HOME`）などの危険なパスが指定された場合、意図しない広範囲のファイル削除につながる。
3. **利用者が追加したファイルの巻き込み削除**: インストール先ディレクトリ全体を無条件に削除する場合、利用者が意図して追加したカスタムファイルや設定が失われる。

### 15.2 基本設計方針

開発用インストーラーの更新処理において、多重ネストを確実に防止しつつ、更新失敗時の耐性とパスの安全性を担保する設計を採用する。

| 項目                    | 設計方針                                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------- |
| 更新の基本目的          | リポジトリ正本（`skills/herdr-orchestrator/`）とユーザーグローバル Skill ディレクトリの完全同期（ミラーリング配置） |
| 多重ネスト防止          | コピー先の配下に同名ディレクトリがネストすることを防止し、常に正規の階層構造を維持する                              |
| `--dest` のパス検証     | ルート（`/`）やホーム直下（`$HOME`）、空文字などの危険なパスを拒絶する                                              |
| 失敗耐性（Fail-Closed） | コピー失敗時に既存の正常なインストール環境を破壊しない（ステージング展開またはロールバック保護）                    |
| 原子的な配置切替        | 一時ディレクトリへの展開成功を確認した上でリネーム（`mv`）による置換を行う                                          |

### 15.3 安全な置換方式の仕様

#### 1. `--dest` パスの事前検証

`--dest` に指定されたパスに対して、以下の検証を実施し、不正・危険なパスである場合は処理を開始せずエラー終了（exit 1）する：

- 空文字または空白のみのパス
- ルートディレクトリ（`/`）
- ホームディレクトリ直下（`$HOME` または `~`）
- システム重要ディレクトリ（`/bin`, `/usr`, `/etc`, `/var`, `/tmp` 等）
- 親ディレクトリが存在しない、または書き込み権限がないパス

#### 2. ステージング展開と原子的リネーム

既存ディレクトリをいきなり削除（`rm -rf`）するのではなく、一時ステージングディレクトリを用いて安全に置換する：

1. **一時ディレクトリへの展開**:
   - コピー先と同じ親ディレクトリ内に、一時ステージングディレクトリ（例: `${dst}.tmp.$$`）を作成する。
   - 正本から一時ディレクトリへ `cp -a` でコピーを実行する。
2. **検証**:
   - 一時ディレクトリへのコピーが正常終了したことを確認する。
3. **安全な置換**:
   - 既存の `${dst}` が存在する場合は、一時バックアップ（例: `${dst}.bak.$$`）へ退避（`mv`）するか、コピー成功確定後に置換する。
   - 一時ステージングディレクトリ `${dst}.tmp.$$` を正式な `${dst}` へアトミックにリネーム（`mv`）する。
   - バックアップ `${dst}.bak.$$` を削除する。
4. **中断・エラー時のトラップ（`trap`）**:
   - スクリプト実行中のシグナル受信やエラー終了（`set -e`）時に `trap` を発動し、残存する `${dst}.tmp.$$` をクリーンアップし、退避済みの `${dst}.bak.$$` があれば直ちに元へ復旧する。

これにより、コピー処理中にいかなる異常が発生しても、既存の正常なインストール環境が保護され、中途半端な状態やファイルの喪失が防止される。

### 15.4 適用対象スクリプト

以下の 2 つのスクリプトに本仕様を適用する：

1. `skills/herdr-orchestrator/install/dev-local-install.sh`: 開発用ローカルインストールスクリプト（正本）
2. `skills/herdr-orchestrator/install/setup.sh`: Cursor レガシーパス用セットアップスクリプト
