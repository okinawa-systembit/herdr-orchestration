# 開発時ローカルインストール

**git clone を編集しながら** Orchestrator を触る手順。Skill のユーザーグローバル配置は専用スクリプトで行う。  
**配布**（curl \| bash 等）は [distribution-install.md](distribution-install.md)（方針のみ）。

## 1. Skill 名・配置先（Cursor / Codex / Agy 共通）

| 項目                            | 値                                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------------- |
| グローバル Skill ディレクトリ名 | **`herdr-orchestration`**（リポジトリ名と同じ綴り。**orchestartion** は誤り → **orchestration**） |
| 配置パス                        | **`~/.agents/skills/herdr-orchestration/`**                                                       |
| 公式 Herdr Skill                | 通常 **`herdr`** 等（別ディレクトリ）。名前衝突しない                                             |
| SKILL.md の `name:`             | **`herdr-orchestration`**（グローバルディレクトリ名と一致）                                       |
| CLI コマンド                    | **`herdr-orchestrator`**（`scripts/herdr-orchestrator`、Skill 名とは別）                          |

公式 **herdr** Skill とだけ名前を分ける。リポジトリ内ソースツリーは `skills/herdr-orchestrator/` のまま。

## 2. 前提

- [§17.1–17.2](../../../docs/herdr-orchestrator/08-adoption-and-verification.md#sec-17-1): Herdr 確認、公式 **herdr** Skill。
- Node.js: `npm install` 後 `npm run test:orchestrator`。
- Agent pane: **`HERDR_ENV=1`**。Orchestrator CLI は pane 内のみ。

## 3. リポジトリ取得とゲート

```bash
git clone <this-repo-url> herdr-orchestration
cd herdr-orchestration
npm install
npm run test:orchestrator
```

## 4. 開発用インストール（必須: Skill を Agent に見せる）

リポジトリ root から:

```bash
skills/herdr-orchestrator/install/dev-local-install.sh --dry-run
skills/herdr-orchestrator/install/dev-local-install.sh
```

または:

```bash
npm run install:orchestrator-dev
```

- **正本**: 常に `skills/herdr-orchestrator/`（編集はここだけ）。
- **コピー先**: `~/.agents/skills/herdr-orchestration/`（上書きコピー）。
- **再実行**: Skill / CLI を直したら **同スクリプトを再実行**してグローバルコピーを更新（配布 install 後の更新と同型）。
- **PATH**: `~/.local/bin/herdr-orchestrator` → install 先 `scripts/herdr-orchestrator` へ **symlink**（`install/link-cli.sh`）。開発・配布で同じ（`--dry-run` 時は未インストール環境でもシミュレーション実行可能）。
- **安全性と多重ネスト防止（PR52-01〜PR52-09）**:
  - 再実行時の多重ネスト（`scripts/scripts` 等）を防止し、常にリポジトリ正本と完全同期する。
  - `--dest` にルート（`/`）やホーム直下（`$HOME`）、システムディレクトリ、リンク切れシンボリックリンクなどの危険なパスが指定された場合は安全に拒絶し、正規化実パスへ整合させる（PR52-04）。
  - `eval` を排除した固定記述子による `flock -n` 排他ロックにより、複数インストーラーの同時実行競合を安全に防止する（PR52-05, PR52-06）。
  - コピー失敗時に既存の正常なインストール環境を保護するため、一意な親コンテナへのバックアップ退避（PR52-02）とステージング展開、および `trap` ロールバック保護により失敗耐性を担保する。バックアップ退避直後・フラグ更新前のシグナル割り込みに対しても、バックアップ実体の存在を検知して確実にロールバック復旧する（PR52-08）。
  - ロールバック失敗時も既存バックアップを削除せず保全し、手動復旧コマンドを案内する（PR52-03）。
  - 初回インストール時の配置後失敗時は、不完全な配置ディレクトリをクリーンアップして初期状態へ復旧する（PR52-07）。清掃自体が失敗した場合は警告と手動清掃コマンドを明示的に通知する（PR52-08）。
  - 初回と更新を明示的な状態変数で区別し、推測に基づく誤削除を防止する。またコミット段階に達した後は異常終了時でも新インストール先を絶対に削除せず保持する（PR52-09）。

別パス:

```bash
HERDR_ORCHESTRATION_SKILL_DEST="$HOME/.agents/skills/herdr-orchestration" \
  skills/herdr-orchestrator/install/dev-local-install.sh
```

## 5. Public CLI（開発＝配布後と同じ呼び方）

編集後は **`npm run install:orchestrator-dev` を再実行**してから、PATH 上の CLI を使う（repo 直 PATH は使わない）。

```bash
herdr-orchestrator --help
herdr-orchestrator doctor
which herdr-orchestrator   # 例: ~/.local/bin/herdr-orchestrator
```

`~/.local/bin` が PATH に無い場合のみ:

```bash
export PATH="${HOME}/.local/bin:${PATH}"
```

コマンド一覧: [public-cli.md](public-cli.md)。

## 6. 本リポジトリ内のその他 Skill

| 種別                | パス                            | 扱い             |
| ------------------- | ------------------------------- | ---------------- |
| Repository workflow | `.agents/skills/repo-workflow/` | プロジェクト内   |
| Repository review   | `.agents/skills/repo-review/`   | 本 repo レビュー |

中央 Skill は **グローバルコピー**（§4）で足りる。`.agents/skills/` へ中央 Skill を symlink する方式は使わない（標準配置先は `~/.agents/skills/herdr-orchestration`）。

## 7. Task Registry

`${XDG_STATE_HOME:-~/.local/state}/herdr-orchestrator/tasks/` — 開発コピー・配布コピー・リポジトリ直実行で **同一 CLI なら同じ state**。

## 8. やらないこと

| 避ける                                        | 理由                                                        |
| --------------------------------------------- | ----------------------------------------------------------- |
| 編集後に再インストールしない                  | Agent / `herdr-orchestrator` が古い install 先を指す        |
| repo 内 `skills/.../scripts` を PATH に載せる | 配布後と PATH・Codex rule がずれる                          |
| `setup.sh` を日常の正本にしない               | default が `~/.cursor/skills/herdr-orchestrator` のレガシー |
| Registry JSON 手編集                          | public CLI のみ                                             |

## 9. 関連

- 配布方針: [distribution-install.md](distribution-install.md)
- §17 参照: [adoption.md](adoption.md)
- スクリプト: [install/dev-local-install.sh](../install/dev-local-install.sh)
