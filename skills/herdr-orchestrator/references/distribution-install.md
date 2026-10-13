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

---

## 2. GitHub Actions 自動リリースワークフロー

### 2.1 トリガー方針と安全設計

- **トリガー**: `push: tags: ['v*']` および手動実行 `workflow_dispatch`
- **意図しないリリースの防止**:
  - 通常の `main` へのプルリクエストマージ（リポジトリ専用スキル `.agents/skills/` や設計ドキュメントの修正など）ではアクションは**起動しない**。
  - 開発者が明示的にバージョンタグ（例: `v0.1.0`）をプッシュ、または GitHub Web 画面で Release を作成・公開したときのみ実行される。

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
  workflow_call: # 他のワークフロー（release.yml）からの再利用

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

  # 2. 検証成功後にパッケージングとリリースを実行
  release:
    needs: verify
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write # Release作成・アセット添付に必要

    steps:
      - name: 1. Checkout repository
        uses: actions/checkout@v7

      - name: 2. Verify package source files
        run: |
          for item in SKILL.md scripts references; do
            if [ ! -e "skills/herdr-orchestrator/${item}" ]; then
              echo "Error: Missing required package item: skills/herdr-orchestrator/${item}" >&2
              exit 1
            fi
          done

      - name: 3. Ensure executable permissions & build distribution tarball
        run: |
          VERSION="${GITHUB_REF_NAME:-${{ inputs.tag }}}"
          chmod +x skills/herdr-orchestrator/scripts/herdr-orchestrator \
                   skills/herdr-orchestrator/scripts/task-registry.py
          tar -czf "herdr-orchestration-${VERSION}.tar.gz" \
              -C skills/herdr-orchestrator \
              SKILL.md scripts references

      - name: 4. Generate SHA256 checksum
        run: |
          VERSION="${GITHUB_REF_NAME:-${{ inputs.tag }}}"
          ARCHIVE_NAME="herdr-orchestration-${VERSION}.tar.gz"
          sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256"

      - name: 5. Create GitHub Release and upload assets
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION="${GITHUB_REF_NAME:-${{ inputs.tag }}}"
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

### 3.1 想定 UX

```bash
# 最新版のインストール
curl -fsSL 'https://raw.githubusercontent.com/<org>/herdr-orchestration/main/install.sh' | bash

# バージョン指定・ドライラン
curl -fsSL 'https://raw.githubusercontent.com/<org>/herdr-orchestration/main/install.sh' | bash -s -- --version v0.1.0 --dry-run
```

### 3.2 オプション仕様

- `--version <tag>`: インストールするタグ（デフォルト: 最新リリース）
- `--dest <path>`: Skill 配置先（デフォルト: `~/.agents/skills/herdr-orchestration`）
- `--bin-dir <path>`: CLI リンク先（デフォルト: `~/.local/bin`）
- `--dry-run`: 変更を加えずに実行予定内容を表示
- `-h, --help`: ヘルプを表示

### 3.3 実行処理の流れ

1. **パイプ切断保護**: スクリプト全体を `main() { ... }` 関数でラップし、末尾で `main "$@"` を呼ぶ。
2. **事前環境確認**: `curl`, `tar`, `python3` の存在確認。
3. **バージョン解決**: `--version` が未指定の場合、GitHub Releases API またはリダイレクトから latest タグ名を取得。
4. **アーカイブ取得と検証**: 一時領域に tarball と `.sha256` をダウンロードし、チェックサムを検証。
5. **配置先へ展開**: 既存の配置先をバックアップ／退避したうえで安全に展開。
6. **実行権限設定**: `chmod +x <dest>/scripts/herdr-orchestrator <dest>/scripts/task-registry.py`。
7. **CLI シンボリックリンク**: `~/.local/bin/herdr-orchestrator` -> `<dest>/scripts/herdr-orchestrator`（`link-cli.sh` のロジック）。

---

## 4. アップデート仕様（`herdr-orchestrator update`）

### 4.1 CLI 仕様

```bash
herdr-orchestrator update [--version <tag>] [--dry-run]
```

### 4.2 更新ポリシー

- **自動更新の禁止**: タスク実行中の予期せぬ破壊や競合を防ぐため、バックグラウンドでの自動更新は行わない（[09-maintenance-and-roadmap.md §20.4](../../../docs/herdr-orchestrator/09-maintenance-and-roadmap.md#sec-20-4) の原則に準拠）。
- **明示的実行**: ユーザーまたは管理者が明示的にコマンドを実行したときのみ更新する。
- **実行中タスクの事前確認**: Registry 内に `in_progress` / `dispatched` なタスクが存在する場合、警告を表示して確認を促す。

---

## 5. 開発用インストールとの対比

| 項目           | 開発用（`dev-local-install.sh`）       | 配布用（`install.sh` / `update`）              |
| :------------- | :------------------------------------- | :--------------------------------------------- |
| **入手元**     | ローカル checkout ツリー               | GitHub Release Tarball                         |
| **前提条件**   | git リポジトリクローン済み             | curl, tar, python3（git 不要）                 |
| **配置先**     | `~/.agents/skills/herdr-orchestration` | `~/.agents/skills/herdr-orchestration`（同一） |
| **CLI リンク** | `~/.local/bin/herdr-orchestrator`      | `~/.local/bin/herdr-orchestrator`（同一）      |
| **更新方法**   | `npm run install:orchestrator-dev`     | `herdr-orchestrator update`                    |
