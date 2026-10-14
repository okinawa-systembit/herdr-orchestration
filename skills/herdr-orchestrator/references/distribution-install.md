# 配布インストール・リリース・更新仕様

開発時は [dev-local-install.md](dev-local-install.md) と `install/dev-local-install.sh` を使う。本書は **clone なしで導入・更新する配布環境** の正式仕様。パッケージマネージャ（npm/pip 等）は使わず、GitHub Releases の Release Tarball を活用した `curl | bash` インストールと、`herdr-orchestrator update` CLI による更新を提供する。

※ **対象動作環境**: **WSL2（Ubuntu）** 限定。Windows ネイティブ環境および macOS への対応は不要。電源断、WSL 強制終了、SIGKILL からの自動復旧は保証対象外とし、通常のスクリプトエラーやシグナル中断に対する既存インストールの保護・復旧を保証する。

---

## 1. 配布パッケージ仕様（Release Tarball）

### 1.1 アーカイブ構成

配布パッケージはリポジトリ全体のソースではなく、Skill 実行に必要なファイルツリーのみで構成する。

- **アーカイブ名**: `herdr-orchestration-vX.Y.Z.tar.gz`
- **チェックサム**: `herdr-orchestration-vX.Y.Z.tar.gz.sha256`
- **アーカイブ含有物**:
  - `SKILL.md` (Skill 定義正本)
  - `scripts/` (`herdr-orchestrator`, `task-registry.py`, 内部 Python モジュール群)
  - `references/` (エージェント参照用リファレンス群)

※ テスト（`tests/`）、リポジトリ設計書（`docs/`）、CI 設定、開発用スクリプトはパッケージに含めない。
※ アーカイブ内には通常ファイルおよびディレクトリのみを含め、シンボリックリンク、ハードリンク、特殊デバイスファイル等は含めない。

### 1.2 アーカイブ作成コマンド

```bash
tar -czf "herdr-orchestration-${VERSION}.tar.gz" \
    -C skills/herdr-orchestrator \
    SKILL.md scripts references
```

展開先で直下に `SKILL.md` / `scripts` / `references` が配置されるルート構成とする。

### 1.3 完全性と真正性の保証方針

- **完全性（Integrity）の確認**:
  - SHA256 チェックサムファイル（`.sha256`）を配布物と同一の Release アセットとして提供する。
  - インストーラおよび更新 CLI は、ダウンロード後にローカルでハッシュ値を計算して照合し、通信経路上の欠損やダウンロード破損を検知する。
- **真正性（Authenticity）の担保とリダイレクト制御**:
  - チェックサム照合は「ダウンロードした tarball がチェックサムファイルと一致すること」を保証するものであり、配布元そのものの真正性を単独で証明するものではない。
  - 配布元 URL を公式リポジトリ（`https://github.com/okinawa-systembit/herdr-orchestration`）に厳密に固定し、HTTPS 通信を必須とする。
  - GitHub Release アセット取得時のリダイレクト先は、GitHub が管理する正規の配布ドメイン（`github.com`, `raw.githubusercontent.com`, `*.githubusercontent.com`）に限定し、未知の外部ドメインへのリダイレクトは拒否する。
  - 将来的なセキュリティ要件の強化として、GitHub Artifact Attestations（ビルド証明）や署名検証の導入を検討可能な設計とする。

### 1.4 バージョン命名規則と成果物の不変性ポリシー

- **SemVer 規則の強制**:
  - 正式リリースタグは `^v[0-9]+\.[0-9]+\.[0-9]+$`（例: `v0.1.0`）とする。
  - プレリリースは `^v[0-9]+\.[0-9]+\.[0-9]+-[a-zA-Z0-9.]+$`（例: `v0.1.0-rc.1`）とする。
  - 不正な形式のタグ（例: `v-test`, `v-next`）でのリリース作成はワークフロー冒頭で拒否する。
- **リリースの不変性（Immutability）の徹底**:
  - 一度公開されたリリース成果物は**完全不変（Immutable）**とする。同名タグに対する再パッケージングやアセットの上書き公開（破壊的再公開）は禁止する。
  - 修正が必要な場合は、必ずパッチバージョンをインクリメント（例: `v0.1.1`）して新しいリリースとして公開する。

---

## 2. GitHub Actions 自動リリースワークフロー

### 2.1 トリガー方針と安全設計

- **トリガー**: `push: tags: ['v*']` および手動実行 `workflow_dispatch`
- **意図しないリリースの防止**:
  - 通常の `main` へのプルリクエストマージ（リポジトリ専用スキル `.agents/skills/` や設計ドキュメントの修正など）ではアクションは**起動しない**。
  - 開発者が明示的にバージョンタグをプッシュ、または手動 dispatch したときのみ実行される。
- **手動実行（`workflow_dispatch`）時の実行コンテキストと対象コミット**:
  - GitHub Actions の仕様上、手動実行時のワークフロー定義自体はディスパッチを実行したブランチ（通常 `main`）からロードされるが、`resolve` ジョブで指定タグから解決されたコミット SHA を Reusable CI（`ci.yml`）および `release` ジョブのチェックアウト ref として厳格に引き渡す。これにより、手動実行時でも検証・パッケージング対象のコードは指定タグのコミット SHA に完全に固定される。
  - リリース対象コミットに所定のテスト定義が含まれている前提で動作するため、手動指定時も SemVer に準拠した有効なリリースコミットを指定する。
- **権限（Permissions）の最小化方針**:
  - ワークフロー全体での不用意な権限剥奪（トップレベル設定による意図しない全権限の一律 none 化）を防止しつつ最小権限（Least Privilege）を確立するため、各ジョブ単位で明示的に権限を宣言する。`resolve` ジョブおよび `verify` ジョブ（Reusable CI 含む）には `permissions: contents: read` を付与し、Release 作成が必要な `release` ジョブにのみ `permissions: contents: write` を明示付与するジョブ単位の最小権限ポリシーを採用する。
- **並行実行制御（`concurrency`）の適用範囲と重複実行防止**:
  - プルリクエスト（PR）への連続プッシュ時や、同一ブランチでの連続コミット時に古いアクションが滞留・並行して実行されるのを防ぐため、CI ワークフロー単位で `concurrency` を導入する。
  - `concurrency: group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}, cancel-in-progress: true` により並行制御を行う。
    - **キーの分割**: PR イベント時は PR 番号（`${{ github.event.pull_request.number }}`）をキーとし、同一 PR への連続 push 時の先行ジョブを自動キャンセルする。push イベント時は Git ref（`${{ github.ref }}`、例: `refs/heads/main`）をキーとし、同一ブランチへの連続 push 時の先行ジョブを自動キャンセルする。
    - **PR と main 実行の独立性**: PR 番号とブランチ名は異なるグループキーとなるため、PR の実行とマージ後の main push 実行は相互にキャンセルせず、それぞれの最新状態が独立して検証される。
    - **Release ワークフローへの非適用**: `release.yml` には無条件に `cancel-in-progress: true` を適用しない。タグ検証、tarball ビルド、アセット添付などの不可逆な公開処理が途中で強制中断されるリスクを回避するためである。
    - **キャンセルの性質**: `concurrency` は先行ジョブへのキャンセル要求であり、ステップ実行中のタイミングによっては即時停止しない場合がある。
- **PR マージ結合テストの維持と参照消失エラー対策**:
  - **マージ結合テストの維持**: `pull_request` イベントでは、PR ブランチ単体ではなく最新の base（`main`）と結合した「仮想マージ結果（`refs/pull/<PR番号>/merge`）」をテストすることで、マージ後に発生し得る競合やテスト破損を事前に検知する品質保証水準（マージゲート）を維持する。
  - **参照消失とジョブ起動の競合モデル**: PR がマージまたはクローズされると、GitHub 側の仮想マージ参照（`refs/pull/<PR番号>/merge`）は即座に削除・無効化される。ジョブ実行条件 `if: github.event.pull_request == null || github.event.pull_request.state == 'open'` は、イベント発火時点で既にクローズされていた不要な実行（遅延起動や手動再実行など）をスキップするガードとして機能する。
  - **競合の限界と複合アプローチ**: ただし、`github.event.pull_request.state` はワークフロー起動時のイベントペイロードのスナップショットであるため、キュー滞留中に PR がマージ・クローズされた場合、参照消失とチェックアウト実行の競合を完全に排除できるものではない。このため、`concurrency` による古い実行の即座キャンセルによってキュー滞留時間を極小化し、競合発生確率を低減する設計とする。万一マージ直後に取り残された古い PR ジョブで参照消失による checkout エラーが生じた場合でも、マージ後の `main` への push CI が独立して実行されて最新コミットが検証されるため、リポジトリ全体の整合性は維持される。
  - **Reusable 呼び出し時の優先度**: Reusable Workflow（`workflow_call`）から `inputs.target_ref` が渡された場合はそれが最優先され、タグ・コミット固定のリリース検証要件も完全に維持する。

### 2.2 採用アクションライブラリ（最新版）

| ライブラリ             | 採用バージョン  | 役割                                             |
| :--------------------- | :-------------- | :----------------------------------------------- |
| `actions/checkout`     | `v7` (`v7.0.1`) | リポジトリの安全なチェックアウト                 |
| `actions/setup-python` | `v7` (`v7.0.0`) | Python 3 実行環境（ユニットテスト用）            |
| `actions/setup-node`   | `v7`            | Node.js 実行環境（Prettier / markdownlint 用）   |
| `gh` CLI               | ランナー標準    | 公式 GitHub CLI によるリリース作成・アセット添付 |

### 2.3 ワークフロー設計（CI・Release の検証対象コミット一致保証）

テスト・リント検証手順を重複定義しないため、GitHub Actions の **Reusable Workflows (`workflow_call`)** を採用する。

手動リリース（`workflow_dispatch`）時にも CI と配布物が同一コミットを対象とすることを保証するため、**最初の `resolve` ジョブで解決した同一のタグ/ref を Reusable CI（`ci.yml`）と `release` ジョブの双方に渡す構成**とする。

また、プロセスハング防止のため `timeout-minutes: 10` を設定し、Node.js は最新 LTS の **Node.js 24** を使用する。

#### ① 共通 CI ワークフロー (`.github/workflows/ci.yml`)

`workflow_call` の入力パラメータとして `target_ref` を受け取り、指定された ref を確実にチェックアウトしてテストする。また、並行制御（`concurrency`）による同一 PR / ブランチの重複排除、およびオープン中 PR の仮想マージ結合テストを安全に実行する。

```yaml
name: CI

on:
  pull_request:
  push:
    branches:
      - main
  workflow_call:
    inputs:
      target_ref:
        description: "Target Git ref to checkout and test"
        type: string
        required: false
        default: ""

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  verify:
    if: github.event.pull_request == null || github.event.pull_request.state == 'open'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read

    steps:
      - name: 1. Checkout repository
        uses: actions/checkout@v7
        with:
          ref: ${{ inputs.target_ref || github.ref }}

      - name: 2. Log checked-out commit SHA
        run: |
          echo "Checked out commit SHA: $(git rev-parse HEAD)"

      - name: 3. Setup Python 3
        uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - name: 4. Setup Node.js 24
        uses: actions/setup-node@v7
        with:
          node-version: "24"
          cache: "npm"

      - name: 5. Install npm dependencies
        run: npm ci

      - name: 6. Run orchestrator unit tests
        run: npm run test:orchestrator

      - name: 7. Check markdown linting & formatting
        run: npm run check:md
```

#### ② リリースワークフロー (`.github/workflows/release.yml`)

タグ解決 ➔ 同一タグでの CI 検証 ➔ 同一タグでのビルド・スモークテスト・公開を直列に繋ぎ、検証と配布の乖離を完全に防止する。

```yaml
name: Release Orchestrator

on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      tag:
        description: "Release tag (e.g. v0.1.0)"
        required: true

jobs:
  # 1. 対象タグの解決、SemVer検証、およびコミットSHAの確定
  resolve:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
    outputs:
      tag: ${{ steps.resolve.outputs.tag }}
      commit_sha: ${{ steps.resolve.outputs.commit_sha }}

    steps:
      - name: 1. Checkout repository to resolve commit SHA
        uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - name: 2. Resolve and validate target tag and commit SHA
        id: resolve
        run: |
          TAG="${{ inputs.tag || github.ref_name }}"
          if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
            echo "Error: Invalid tag format: $TAG (must follow SemVer, e.g. v0.1.0)" >&2
            exit 1
          fi
          if ! git rev-parse "refs/tags/${TAG}" > /dev/null 2>&1; then
            echo "Error: Tag refs/tags/${TAG} does not exist" >&2
            exit 1
          fi
          COMMIT_SHA=$(git rev-parse "refs/tags/${TAG}^{commit}")
          echo "Resolved tag: $TAG"
          echo "Resolved commit SHA: $COMMIT_SHA"
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"
          echo "commit_sha=${COMMIT_SHA}" >> "$GITHUB_OUTPUT"

  # 2. 確定された同一コミットSHAに対して共通 CI を実行
  verify:
    needs: resolve
    permissions:
      contents: read
    uses: ./.github/workflows/ci.yml
    with:
      target_ref: ${{ needs.resolve.outputs.commit_sha }}

  # 3. 検証成功後に同一コミットSHAからパッケージング、スモークテスト、リリースを実行
  release:
    needs: [resolve, verify]
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write

    steps:
      - name: 1. Checkout resolved commit SHA
        uses: actions/checkout@v7
        with:
          ref: ${{ needs.resolve.outputs.commit_sha }}
          fetch-depth: 0

      - name: 2. Verify tag commit match and immutability
        run: |
          TARGET_TAG="${{ needs.resolve.outputs.tag }}"
          EXPECTED_SHA="${{ needs.resolve.outputs.commit_sha }}"
          HEAD_SHA=$(git rev-parse HEAD)
          echo "Target tag:   $TARGET_TAG"
          echo "Expected SHA: $EXPECTED_SHA"
          echo "HEAD SHA:     $HEAD_SHA"

          # 1. HEAD が解決済みコミット SHA と一致することを確認
          if [[ "$HEAD_SHA" != "$EXPECTED_SHA" ]]; then
            echo "Error: Checkout HEAD SHA ($HEAD_SHA) does not match expected SHA ($EXPECTED_SHA)" >&2
            exit 1
          fi

          # 2. リモートの最新タグ参照を git ls-remote で直接取得（タグ移動・削除・改ざんの検出）
          echo "Checking remote tag reference via git ls-remote..."
          if ! LS_REMOTE_OUT=$(git ls-remote origin "refs/tags/${TARGET_TAG}" "refs/tags/${TARGET_TAG}^{}"); then
            echo "Error: Failed to query remote tag references from origin via git ls-remote" >&2
            exit 1
          fi
          if [ -z "$LS_REMOTE_OUT" ]; then
            echo "Error: Remote tag refs/tags/${TARGET_TAG} does not exist on origin" >&2
            exit 1
          fi

          # Annotated tag の剥離コミット SHA (^{}) を優先取得、なければ直接のタグ SHA を取得
          REMOTE_TAG_SHA=$(echo "$LS_REMOTE_OUT" | awk -v tag="refs/tags/${TARGET_TAG}^{}" '$2 == tag {print $1}')
          if [ -z "$REMOTE_TAG_SHA" ]; then
            REMOTE_TAG_SHA=$(echo "$LS_REMOTE_OUT" | awk -v tag="refs/tags/${TARGET_TAG}" '$2 == tag {print $1}')
          fi

          echo "Current Remote Tag SHA: $REMOTE_TAG_SHA"
          if [[ "$REMOTE_TAG_SHA" != "$EXPECTED_SHA" ]]; then
            echo "Error: Remote tag SHA ($REMOTE_TAG_SHA) has moved or differs from expected SHA ($EXPECTED_SHA)" >&2
            exit 1
          fi

      - name: 3. Verify package source files
        run: |
          for item in SKILL.md scripts references; do
            if [ ! -e "skills/herdr-orchestrator/${item}" ]; then
              echo "Error: Missing required package item: skills/herdr-orchestrator/${item}" >&2
              exit 1
            fi
          done

      - name: 4. Ensure executable permissions & build distribution tarball
        run: |
          VERSION="${{ needs.resolve.outputs.tag }}"
          chmod +x skills/herdr-orchestrator/scripts/herdr-orchestrator \
                   skills/herdr-orchestrator/scripts/task-registry.py
          tar -czf "herdr-orchestration-${VERSION}.tar.gz" \
              -C skills/herdr-orchestrator \
              SKILL.md scripts references

      - name: 5. Generate SHA256 checksum
        run: |
          VERSION="${{ needs.resolve.outputs.tag }}"
          ARCHIVE_NAME="herdr-orchestration-${VERSION}.tar.gz"
          sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256"

      - name: 6. Smoke test distribution tarball
        run: |
          VERSION="${{ needs.resolve.outputs.tag }}"
          ARCHIVE_NAME="herdr-orchestration-${VERSION}.tar.gz"
          TEST_DIR=$(mktemp -d)
          echo "Testing archive in ${TEST_DIR}..."
          tar -xzf "${ARCHIVE_NAME}" -C "${TEST_DIR}"
          # 必須ファイルの存在確認
          test -f "${TEST_DIR}/SKILL.md"
          test -x "${TEST_DIR}/scripts/herdr-orchestrator"
          test -x "${TEST_DIR}/scripts/task-registry.py"
          # CLI ヘルプ実行確認
          "${TEST_DIR}/scripts/herdr-orchestrator" --help > /dev/null
          # チェックサム検証確認
          sha256sum -c "${ARCHIVE_NAME}.sha256"
          rm -rf "${TEST_DIR}"
          echo "Smoke test passed successfully."

      - name: 7. Check release existence (Immutability & HTTP status code classification)
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION="${{ needs.resolve.outputs.tag }}"
          REPO="${{ github.repository }}"
          echo "Checking release existence for ${VERSION} in ${REPO}..."

          # GitHub REST API で HTTP ステータスコードを直接取得（通信失敗も明示検出）
          if ! HTTP_STATUS=$(curl -sS -o /dev/null -w "%{http_code}" \
            -H "Authorization: token ${GH_TOKEN}" \
            -H "Accept: application/vnd.github.v3+json" \
            "https://api.github.com/repos/${REPO}/releases/tags/${VERSION}"); then
            echo "Error: Failed to communicate with GitHub API via curl" >&2
            exit 1
          fi

          echo "GitHub API HTTP Status: ${HTTP_STATUS}"
          if [ "${HTTP_STATUS}" = "200" ]; then
            echo "Error: Release ${VERSION} already exists (HTTP 200). Immutable release policy forbids overwriting." >&2
            exit 1
          elif [ "${HTTP_STATUS}" = "404" ]; then
            echo "Confirmed: Release ${VERSION} does not exist (HTTP 404). Proceeding with release."
          elif [ "${HTTP_STATUS}" = "401" ] || [ "${HTTP_STATUS}" = "403" ]; then
            echo "Error: Authentication or permission error while checking release (HTTP ${HTTP_STATUS})" >&2
            exit 1
          else
            echo "Error: Unexpected API response or network failure while checking release (HTTP ${HTTP_STATUS})" >&2
            exit 1
          fi

      - name: 8. Create GitHub Release and upload assets
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION="${{ needs.resolve.outputs.tag }}"
          ARCHIVE_NAME="herdr-orchestration-${VERSION}.tar.gz"
          gh release create "${VERSION}" \
              "${ARCHIVE_NAME}" \
              "${ARCHIVE_NAME}.sha256" \
              --title "${VERSION}" \
              --generate-notes
```

---

## 3. 配布用インストーラ（`install.sh`）仕様

### 3.1 想定 UX と取得元

- **配布元 URL**: `https://raw.githubusercontent.com/okinawa-systembit/herdr-orchestration/main/install.sh`（公式リポジトリに固定）
- **推奨パイプ実行（標準）**:
  ```bash
  # 最新安定版のインストール
  curl -fsSL 'https://raw.githubusercontent.com/okinawa-systembit/herdr-orchestration/main/install.sh' | bash

  # バージョン指定・ドライラン
  curl -fsSL 'https://raw.githubusercontent.com/okinawa-systembit/herdr-orchestration/main/install.sh' | bash -s -- --version v0.1.0 --dry-run
  ```
- **監査・検証向け 2 ステップ手順（代替手順）**:
  スクリプトの内容を事前に監査・確認したい利用者向けの手順。
  ```bash
  curl -fsSL 'https://raw.githubusercontent.com/okinawa-systembit/herdr-orchestration/main/install.sh' -o /tmp/herdr-install.sh
  # スクリプト内容を確認
  less /tmp/herdr-install.sh
  # 実行
  bash /tmp/herdr-install.sh --version v0.1.0
  rm /tmp/herdr-install.sh
  ```

### 3.2 オプション仕様

- `--version <tag>`: インストールするタグ（デフォルト: 最新安定版リリース。プレリリースは除外）
- `--dest <path>`: Skill 配置先（デフォルト: `~/.agents/skills/herdr-orchestration`）
- `--bin-dir <path>`: CLI リンク先（デフォルト: `~/.local/bin`）
- `--dry-run`: 一切のファイル変更を行わずに実行予定内容を表示
- `--force`: 既存の開発インストールや実行中タスクの警告を明示的にオーバーライド（コンソールに危険性を警告表示した上で続行）
- `-h, --help`: ヘルプを表示

### 3.3 安全な配置切替と復旧仕様

インストーラは途中で障害が発生しても、既存の正常なインストール環境を破壊しないよう安全に動作する。各 rename 操作は原子的だが、切替全体としてはミリ秒単位の移行期間が存在することを前提とし、プロセス内異常時は自動ロールバック復旧する。

1. **パイプ切断保護**: スクリプト全体を `main() { ... }` 関数でラップし、末尾で `main "$@"` を呼ぶ。冒頭に `set -euo pipefail` を指定し、パイプラインの途中エラーを確実に検知する。
2. **事前環境確認**: `curl`, `tar`, `python3`, `flock` の存在確認。
3. **インストール排他ロック**: `~/.agents/skills/.herdr-orchestrator.install.lock` に対し、`flock -n 200`（ファイルディスクリプタ 200）による非ブロッキング排他ロックを取得。親 Bash プロセスが生存している間ロックが確実に保持され、複数インストーラや更新処理の同時並行実行を防止する。正常終了時および異常終了時にプロセス終了とともに自動解放される。
4. **バージョン解決**: `--version` が未指定の場合、GitHub Releases API からプレリリースを除いた最新安定版（`latest`）タグ名を取得する。
5. **一時領域へのダウンロード**: 一時作業ディレクトリ（`mktemp -d`）に tarball と `.sha256` をダウンロードする（GitHub 管理下の正規ホストへの HTTPS リダイレクトのみ許可）。
6. **完全性検証**: ダウンロードした tarball の SHA256 ハッシュを計算し、`.sha256` ファイルと厳密に照合する。不一致時は即座に中断する。
7. **アーカイブ安全性検証（Path Traversal / 不正リンク防止）**:
   展開前に `tar -ztf` および `tar -tvf` でアーカイブ内容の属性とファイル一覧を検査する。
   - 絶対パス（`/` で始まるパス）が含まれていないこと。
   - 親ディレクトリへの脱出（`..`）が含まれていないこと。
   - シンボリックリンク、ハードリンク、デバイスファイル、特殊ファイルが含まれていないこと（一般ファイルとディレクトリのみ許可）。
   - 許可ツリー（`SKILL.md`, `scripts/*`, `references/*`）以外の想定外ファイルが含まれていないこと。
8. **Staging 展開と事前動作検証**:
   - 一時ステージングディレクトリ（例: `~/.agents/skills/.herdr-orchestration.staging-XXXX`）に tarball を展開する。
   - 実行権限を設定（`chmod +x scripts/herdr-orchestrator scripts/task-registry.py`）。
   - `scripts/herdr-orchestrator --help` を実行し、Python ランタイムおよび CLI が正常に動作することを事前検証する。
9. **配置切替と障害モデル**:
   - 検証が完全に成功した後、既存の配置先（`~/.agents/skills/herdr-orchestration`）が存在する場合は一時バックアップ（`~/.agents/skills/.herdr-orchestration.backup`）へ `mv` リネーム退避し、ステージングディレクトリを正式配置先パスへ `mv` リネーム配置する。初回インストール時（旧配置が存在しない場合）はバックアップなしでステージングを配置する。
   - ※ 2 回の `mv` 間に生じる極めて短い移行期間（ミリ秒単位）が存在することを許容する。
   - **復旧モデルの境界**:
     - _プロセス内障害（通信断、tar 展開失敗、検証失敗、新配置移動失敗、symlink 作成失敗、および SIGINT / SIGTERM / SIGHUP シグナル中断）_:
       - `trap` ハンドラにより、更新時であれば退避先バックアップから元の状態へ自動ロールバック復旧する（既存配置の退避完了直後からロールバック可能状態として記録され、新配置移動失敗時にも即座に旧バージョンへ復元される）。
       - ロールバック処理の成否（`mv` の成功）を確認し、成否を明示する（復旧後の必須ファイル検査は健全性通知として分離し、ファイル操作自体の成功判定を阻害しない）。
       - ロールバック失敗時はバックアップの保存場所とともに、配置先ディレクトリの確認・クリーンアップ（`ls -ld`, `rm -rf`）および手動復旧（`mv`）の手順をエラー出力し、バックアップを削除せず保全する。
       - 初回インストール時の配置後失敗時は、不完全な配置ディレクトリを削除して初期状態へクリーンアップする。
     - _外部強制終了（SIGKILL、電源断、WSL 強制終了）_:
       - プロセスや OS がシグナル捕捉不能な形で突然死する障害からの自動復旧は本ツールの保証対象外とする。
10. **CLI シンボリックリンクの更新と既存ファイル保護**:
    - `~/.local/bin/herdr-orchestrator` -> `<dest>/scripts/herdr-orchestrator` を更新する。
    - リンク先が存在しない場合は新規作成、既存シンボリックリンクは更新する。
    - リンク先に通常ファイルまたはディレクトリが既に存在する場合は、誤上書きを防ぐためデフォルトで拒否し、`--force` 指定時のみ上書きを許可する。
11. **正常完了後のクリーンアップ**:
    - 正常完了時は一時バックアップおよび一時ダウンロード領域を削除し、排他ロックを解放する。

---

## 4. アップデート仕様（`herdr-orchestrator update`）

### 4.1 CLI 仕様

```bash
herdr-orchestrator update [--version <tag>] [--force] [--dry-run]
```

### 4.2 実行環境要件

- **`HERDR_ENV=1` の免除**:
  - `update` コマンドはエージェントセッション外や Herdr 未起動のターミナルから人間・管理者が実行することが想定されるため、通常の `handoff` や `task-status` 等と異なり、**`HERDR_ENV=1` の環境制約を免除**する。

### 4.3 更新ポリシー・排他制御と運用制約

- **自動更新の禁止**:
  - タスク実行中の予期せぬ破壊や競合を防ぐため、バックグラウンドでの自動更新は行わない（[09-maintenance-and-roadmap.md §20.4](../../../docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20-4) の原則に準拠）。
- **インストール・更新処理同士の排他制御**:
  - `install.sh` と `herdr-orchestrator update` は同一のインストールロック（`~/.agents/skills/.herdr-orchestrator.install.lock` に対する `flock` 排他ロック）を使用し、複数インストーラや更新処理が同時並行で実行されることを防止する。
  - 通常の Orchestrator CLI 処理（`handoff`、`result-submit`、`resume-task` 等）にはロック処理を追加せず、既存のタスク処理パイプラインへのオーバーヘッドや複雑性を排除する（`.maintenance.lock` は導入しない）。
- **実行中タスクの事前確認とデフォルト拒否（Fail-Closed）**:
  - 更新処理の開始前に Task Registry を確認し、未完了タスク（`task.status` が `dispatched` または `in_progress`、あるいは `completion.status` が `processing`）が存在する場合、**デフォルトで更新を拒否して即座にエラー終了（exit 1）**する。
  - エラーメッセージには未完了タスクの ID を表示し、タスクの完了を待つか、不要な場合は `recover-completion` 等で解決するよう促す。
  - ※ この確認は Task Registry に基づくベストエフォートの事前確認であり、通常タスク処理との完全な排他をシステム的に保証するものではない。
- **運用上の制約**:
  - 通常タスク処理との完全排他はシステム保証されないため、以下の運用制約を厳守する：
    1. Orchestrator の CLI 処理が実行中の場合、更新操作を行わない。
    2. 更新処理中は、新規の `handoff` や `result-submit` 等を開始しない。
    3. 非同期タスクが未完了の場合は、原則として更新を行わない。
    - 上記の運用制約に違反した場合、更新とタスク処理の競合による正常動作は保証されない。
- **強制更新オプション (`--force`)**:
  - 管理者が意図的に未完了タスクの存在や開発インストールの警告を無視して更新したい場合のみ、明示的に `--force` フラグを付与することで更新を許可する（実行時にコンソールへ競合や破壊の危険性を明示警告した上で続行する）。
- **開発インストールの保護**:
  - 配置先が git checkout ツリー（開発用ローカルインストール）であると検知された場合、通常の `update` は警告を出して中止する（誤って手元の開発作業を上書き消去することを防止）。上書きには `--force` を要求する。
- **Registry スキーマ互換性**:
  - 新旧 CLI 間で canonical schema の下位互換性を維持し、更新後も既存のタスクログや結果が正常に読み取り・復旧可能であることを保証する。

---

## 5. 開発用インストールとの対比

| 項目                 | 開発用（`dev-local-install.sh`）       | 配布用（`install.sh` / `update`）                  |
| :------------------- | :------------------------------------- | :------------------------------------------------- |
| **入手元**           | ローカル checkout ツリー               | GitHub Release Tarball                             |
| **前提条件**         | git リポジトリクローン済み             | curl, tar, python3, flock（git 不要、WSL2 Ubuntu） |
| **配置先**           | `~/.agents/skills/herdr-orchestration` | `~/.agents/skills/herdr-orchestration`（同一）     |
| **CLI リンク**       | `~/.local/bin/herdr-orchestrator`      | `~/.local/bin/herdr-orchestrator`（同一）          |
| **切り替え・復旧**   | 手動再実行                             | ステージング切替 (各mv) + 自動ロールバック         |
| **実行中タスク制御** | 開発者の責任                           | デフォルト更新拒否 (Fail-Closed) + 運用制約        |
| **更新方法**         | `npm run install:orchestrator-dev`     | `herdr-orchestrator update`                        |
