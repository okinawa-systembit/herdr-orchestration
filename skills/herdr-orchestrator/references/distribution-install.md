# 配布インストール・リリース・更新仕様

開発時は [dev-local-install.md](dev-local-install.md) と `install/dev-local-install.sh` を使う。本書は **clone なしで導入・更新する配布環境** の正式仕様。パッケージマネージャ（npm/pip 等）は使わず、GitHub Releases の Release Tarball を活用した `curl | bash` インストールと、`herdr-orchestrator update` CLI による更新を提供する。

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
- **真正性（Authenticity）の担保**:
  - チェックサム照合は「ダウンロードした tarball がチェックサムファイルと一致すること」を保証するものであり、配布元そのものの真正性を単独で証明するものではない。
  - 配布元 URL を GitHub 公式 HTTPS エンドポイントかつ公式リポジトリ（`https://github.com/okinawa-systembit/herdr-orchestration`）に厳密に固定し、外部ドメインへのリダイレクトを拒否する。
  - 将来的なセキュリティ要件の強化として、GitHub Artifact Attestations（ビルド証明）や署名検証の導入を検討可能な設計とする。

### 1.4 バージョン命名規則と不変性ポリシー

- **SemVer 規則の強制**:
  - 正式リリースタグは `^v[0-9]+\.[0-9]+\.[0-9]+$`（例: `v0.1.0`）とする。
  - プレリリースは `^v[0-9]+\.[0-9]+\.[0-9]+-[a-zA-Z0-9.]+$`（例: `v0.1.0-rc.1`）とする。
  - 不正な形式のタグ（例: `v-test`, `v-next`）でのリリース作成はワークフロー冒頭で拒否する。
- **リリースの不変性（Immutability）**:
  - 一度公開されたリリース成果物は原則として不変とする。同名タグに対する破壊的な再パッケージングやアセットの上書き公開は禁止する。
  - ワークフロー再実行時（同一コミットでの障害リトライ）に限り、安全にアセットを更新できるよう制御する。

---

## 2. GitHub Actions 自動リリースワークフロー

### 2.1 トリガー方針と安全設計

- **トリガー**: `push: tags: ['v*']` および手動実行 `workflow_dispatch`
- **意図しないリリースの防止**:
  - 通常の `main` へのプルリクエストマージ（リポジトリ専用スキル `.agents/skills/` や設計ドキュメントの修正など）ではアクションは**起動しない**。
  - 開発者が明示的にバージョンタグをプッシュ、または手動 dispatch したときのみ実行される。

### 2.2 採用アクションライブラリ（最新版）

| ライブラリ             | 採用バージョン  | 役割                                             |
| :--------------------- | :-------------- | :----------------------------------------------- |
| `actions/checkout`     | `v7` (`v7.0.1`) | リポジトリの安全なチェックアウト                 |
| `actions/setup-python` | `v5`            | Python 3 実行環境（ユニットテスト用）            |
| `actions/setup-node`   | `v7`            | Node.js 実行環境（Prettier / markdownlint 用）   |
| `gh` CLI               | ランナー標準    | 公式 GitHub CLI によるリリース作成・アセット添付 |

### 2.3 ワークフロー設計（共通 CI と リリースの分離・再利用）

テスト・リント検証手順を重複定義しないため、GitHub Actions の **Reusable Workflows (`workflow_call`)** を採用する。日常の PR / `main` push では `ci.yml` が単体で走り、リリースタグ push 時には `release.yml` が `ci.yml` を呼び出して検証を通過した後にパッケージング・公開を行う。

また、プロセスハングによる不要な課金・占有を防ぐため `timeout-minutes: 10` を設定し、Node.js は最新 LTS の **Node.js 24** を使用する。

#### ① 共通 CI ワークフロー (`.github/workflows/ci.yml`)

```yaml
name: CI

on:
  pull_request:
  push:
    branches:
      - main
  workflow_call:

jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: 1. Checkout repository
        uses: actions/checkout@v7

      - name: 2. Setup Python 3
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 3. Setup Node.js 24
        uses: actions/setup-node@v7
        with:
          node-version: "24"
          cache: "npm"

      - name: 4. Install npm dependencies
        run: npm ci

      - name: 5. Run orchestrator unit tests
        run: npm run test:orchestrator

      - name: 6. Check markdown linting & formatting
        run: npm run check:md
```

#### ② リリースワークフロー (`.github/workflows/release.yml`)

手動リリース時のタグ・コミット不一致を防ぐため、対象タグを解決して明示的に checkout し、タグと HEAD SHA の一致を保証する。また、Release 公開直前に**配布用 tarball 自体のスモークテスト**を実施する。

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
  # 1. 共通 CI ワークフローを再利用してテスト・リント検証
  verify:
    uses: ./.github/workflows/ci.yml

  # 2. 検証成功後にパッケージング、スモークテスト、リリースを実行
  release:
    needs: verify
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write

    steps:
      - name: 1. Resolve and validate target tag
        id: resolve
        run: |
          TAG="${{ inputs.tag || github.ref_name }}"
          if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
            echo "Error: Invalid tag format: $TAG (must follow SemVer, e.g. v0.1.0)" >&2
            exit 1
          fi
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"

      - name: 2. Checkout target tag
        uses: actions/checkout@v7
        with:
          ref: ${{ steps.resolve.outputs.tag }}

      - name: 3. Verify tag commit match
        run: |
          TARGET_TAG="${{ steps.resolve.outputs.tag }}"
          TAG_SHA=$(git rev-list -n 1 "$TARGET_TAG")
          HEAD_SHA=$(git rev-parse HEAD)
          echo "Target tag: $TARGET_TAG"
          echo "Tag SHA:    $TAG_SHA"
          echo "HEAD SHA:   $HEAD_SHA"
          if [[ "$TAG_SHA" != "$HEAD_SHA" ]]; then
            echo "Error: Tag SHA ($TAG_SHA) does not match checkout HEAD SHA ($HEAD_SHA)" >&2
            exit 1
          fi

      - name: 4. Verify package source files
        run: |
          for item in SKILL.md scripts references; do
            if [ ! -e "skills/herdr-orchestrator/${item}" ]; then
              echo "Error: Missing required package item: skills/herdr-orchestrator/${item}" >&2
              exit 1
            fi
          done

      - name: 5. Ensure executable permissions & build distribution tarball
        run: |
          VERSION="${{ steps.resolve.outputs.tag }}"
          chmod +x skills/herdr-orchestrator/scripts/herdr-orchestrator \
                   skills/herdr-orchestrator/scripts/task-registry.py
          tar -czf "herdr-orchestration-${VERSION}.tar.gz" \
              -C skills/herdr-orchestrator \
              SKILL.md scripts references

      - name: 6. Generate SHA256 checksum
        run: |
          VERSION="${{ steps.resolve.outputs.tag }}"
          ARCHIVE_NAME="herdr-orchestration-${VERSION}.tar.gz"
          sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256"

      - name: 7. Smoke test distribution tarball
        run: |
          VERSION="${{ steps.resolve.outputs.tag }}"
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

      - name: 8. Create GitHub Release and upload assets
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION="${{ steps.resolve.outputs.tag }}"
          ARCHIVE_NAME="herdr-orchestration-${VERSION}.tar.gz"
          gh release create "${VERSION}" \
              "${ARCHIVE_NAME}" \
              "${ARCHIVE_NAME}.sha256" \
              --title "Release ${VERSION}" \
              --clobber \
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
- `--force`: 既存の開発インストールや実行中タスクの警告を無視して強制上書き
- `-h, --help`: ヘルプを表示

### 3.3 原子的切替（Atomic Switch）と復旧仕様

インストーラは途中でネットワーク切断や障害が発生しても、既存の正常なインストール環境を破壊しないよう原子的（Atomic）に動作する。

1. **パイプ切断保護**: スクリプト全体を `main() { ... }` 関数でラップし、末尾で `main "$@"` を呼ぶ。冒頭に `set -euo pipefail` を指定し、パイプラインの途中エラーを確実に検知する。
2. **事前環境確認**: `curl`, `tar`, `python3` の存在確認。
3. **排他ロックの取得**: `~/.agents/skills/.herdr-orchestrator.install.lock` に対し `flock` を取得し、複数インストーラや更新処理の同時実行を防止する。
4. **バージョン解決**: `--version` が未指定の場合、GitHub Releases API からプレリリースを除いた最新安定版（`latest`）タグ名を取得する。
5. **一時領域へのダウンロード**: 一時作業ディレクトリ（`mktemp -d`）に tarball と `.sha256` をダウンロードする。
6. **完全性検証**: ダウンロードした tarball の SHA256 ハッシュを計算し、`.sha256` ファイルと厳密に照合する。不一致時は即座に中断する。
7. **パス安全性検証（Path Traversal 防止）**:
   展開前に `tar -ztf` でアーカイブ内容のファイル一覧を検査する。
   - 絶対パス（`/` で始まるパス）が含まれていないこと。
   - 親ディレクトリへの脱出（`..`）が含まれていないこと。
   - 許可ツリー（`SKILL.md`, `scripts/*`, `references/*`）以外の想定外ファイルや特殊デバイスファイルが含まれていないこと。
8. **Staging 展開と事前動作検証**:
   - 一時ステージングディレクトリ（例: `~/.agents/skills/.herdr-orchestration.staging-XXXX`）に tarball を展開する。
   - 実行権限を設定（`chmod +x scripts/herdr-orchestrator scripts/task-registry.py`）。
   - `scripts/herdr-orchestrator --help` を実行し、Python ランタイムおよび CLI が正常に動作することを事前検証する。
9. **原子的置換（Atomic Switch）**:
   - 検証が完全に成功した後、既存の配置先（`~/.agents/skills/herdr-orchestration`）が存在する場合は一時バックアップ（`~/.agents/skills/.herdr-orchestration.backup`）へ `mv` リネーム退避する。
   - ステージングディレクトリを正式配置先パスへ `mv` リネーム配置する。
10. **CLI シンボリックリンクの更新**:
    - `~/.local/bin/herdr-orchestrator` -> `<dest>/scripts/herdr-orchestrator` を更新（`link-cli.sh` のロジック）。
11. **ロールバックとクリーンアップ**:
    - 置換処理中に何らかの異常が発生した場合は `trap` ハンドラにより直ちに一時バックアップから元に戻し、ステージング領域を破棄する。
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

### 4.3 更新ポリシーと実行中タスク保護

- **自動更新の禁止**:
  - タスク実行中の予期せぬ破壊や競合を防ぐため、バックグラウンドでの自動更新は行わない（[09-maintenance-and-roadmap.md §20.4](../../../docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20-4) の原則に準拠）。
- **実行中タスクのデフォルト拒否（Fail-Closed）**:
  - 更新処理開始時、Task Registry をスキャンし、進行中タスク（`task.status` が `dispatched` または `in_progress`、あるいは `completion.status` が `processing`）が存在する場合、**デフォルトで更新を即座にエラー終了（exit 1）**する。
  - エラーメッセージには実行中タスクの ID を表示し、タスクの完了を待つか、不要な場合は `recover-completion` 等で解決するよう促す。
- **強制更新オプション (`--force`)**:
  - 管理者が意図的に実行中タスクの存在を無視して更新したい場合のみ、明示的に `--force` フラグを付与することで更新を続行可能とする。
- **開発インストールの保護**:
  - 配置先が git checkout ツリー（開発用ローカルインストール）であると検知された場合、通常の `update` は警告を出して中止する（誤って手元の開発ブランチを上書き消去することを防止）。上書きには `--force` を要求する。
- **更新中の排他制御**:
  - 更新処理中はインストールロックを取得し、更新作業中に同一マシン上のエージェントから新規の `handoff` や `result-submit` が並行して走らないようにする。
- **Registry スキーマ互換性**:
  - 新旧 CLI 間で canonical schema の下位互換性を維持し、更新後も既存のタスクログや結果が正常に読み取り・復旧可能であることを保証する。

---

## 5. 開発用インストールとの対比

| 項目                 | 開発用（`dev-local-install.sh`）       | 配布用（`install.sh` / `update`）              |
| :------------------- | :------------------------------------- | :--------------------------------------------- |
| **入手元**           | ローカル checkout ツリー               | GitHub Release Tarball                         |
| **前提条件**         | git リポジトリクローン済み             | curl, tar, python3（git 不要）                 |
| **配置先**           | `~/.agents/skills/herdr-orchestration` | `~/.agents/skills/herdr-orchestration`（同一） |
| **CLI リンク**       | `~/.local/bin/herdr-orchestrator`      | `~/.local/bin/herdr-orchestrator`（同一）      |
| **切り替え・復旧**   | 手動再実行                             | 原子的リネーム + 自動ロールバック              |
| **実行中タスク制御** | 開発者の責任                           | デフォルト拒否（`--force` でオーバーライド）   |
| **更新方法**         | `npm run install:orchestrator-dev`     | `herdr-orchestrator update`                    |
