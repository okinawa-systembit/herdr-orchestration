# Logical roles と Role Binding

正本: [§4](../../../docs/herdr-orchestrator/02-roles-and-task-contract.md#sec-4)、[herdr-orchestrator-cross-workspace.md](../../../docs/herdr-orchestrator-cross-workspace.md)（グローバル Skill 単体配置時は [design-links.md](design-links.md)）

## 標準ロールと日本語・カスタムロール名

| 標準 Role（Pattern A 対応） | 用途                 |
| --------------------------- | -------------------- |
| `design`                    | 設計・仕様           |
| `reviewer`                  | コード・設計レビュー |
| `implementer`               | 実装と実装伴うテスト |
| `orchestrator`              | 委譲・進行確認       |

- CLI 側では暗黙の自然言語同義語変換テーブルを持たず、**`role set` で登録したロール名（日本語・英語を問わず NFKC 正規化＋ASCII 小文字化した文字列）をそのまま保存・照合する**。
- 上記の標準 4 ロール以外にも、任意の日本語・カスタムロール名（例: `調査`, `レビュワー`, `実装者`, `ログ調査`, `チケット管理`）を Role Binding で自由に登録・指定できる。
- システム予約値 `"unbound"` はロール名として登録・指定できない（未バインドの Requester ペインから送信した際の `requester.role` にのみ使用される）。
- `handoff` 実行時に指定したロールが未登録（`worker_role_not_bound`）だった場合は、エラー出力 JSON の `available_roles`（ローカル / グローバルの登録済みロール一覧）または `herdr-orchestrator role show` で実際の登録名を確認すること。

> [!NOTE]
> **標準ロール名と日本語名の使い分け**:
> `reviewer` と `レビュワー`、`implementer` と `実装者` はそれぞれ**別のロール名**として扱われます。
> `repo-review` や `repo-workflow` などのリポジトリ標準ツールは英語の標準ロール名（`--worker-role reviewer`）で handoff を行うため、連携ツールを使用するワークスペースでは標準ロール名（`reviewer`, `implementer`）で登録し、UI 表示を日本語にしたい場合は `--label "レビュワー"` のように指定することを推奨します。  
> ※ 同様に、`レビュワー` と `レビュアー` のような表記揺れ・送り仮名の違いも**完全に別のロール名**として区別されるため、登録時のロール名と完全一致で指定してください。

## Role と `pane_id` の紐づけ（Role Binding: ローカル / グローバル）

Herdr 本体の自動セッション復元（`resume`）に干渉しないよう、`herdr agent rename` によるエージェント名の変更は行わず、`herdr-orchestrator role` コマンド（`role set` は `HERDR_ENV=1` 必須、`role show` / `role clear` はセッション外からも実行可）で論理 role と `pane_id`（例: `w15:p1`, `w16:p1`）および指紋メタデータ（`targets[<role>]`）を紐づけて管理する。

- **ローカル Role Binding**: 現在の Git リポジトリ（`<uuid>.json`）または Non-Git ディレクトリ（`dir_<hash16>.json`）固有の紐づけ。
- **グローバル Role Binding（`--global`）**: ワークスペースを跨いでどのディレクトリからでも呼び出せる共通ロール（`global.json`）。
- **UI ラベルの自動連動と残存ロール保護**: `role set` 実行時に `herdr pane rename <pane_id> <display_name>` で Herdr UI 上のペイン表示名にも自動反映される（`role clear` 時は、そのペインが他スコープで現役ロールを兼任していれば残存ロールの表示名が維持され、完全に未バインドとなった場合のみ `<pane_id> --clear` で解除）。※ UI ラベルは 100% 人間向けの表示用であり、照合や自動追従の判定ロジックには一切使用しない。

> [!TIP]
> **ロール設定・スコープ変更時のベストプラクティス**:
>
> - **人間向け表示名の明示**: ロール登録時は、人間が Herdr UI 上で一目で役割を把握できるよう、常に `--label "<人間向け表示名>"`（例: `--label "チケット管理 (共通)"`）を明示することを推奨します。
> - **スコープ変更（ローカル⇔グローバル）時の2段階移行手順**: ロールをローカルからグローバルへ（またはその逆へ）変更する際は、先に `role clear` を実行すると一時的にラベルが途切れてしまうため、**① 新スコープで `role set <role> --label "<表示名>" [--global]`** を実行し、続けて **② 旧スコープで必ず `role clear <role> [--global]`** を実行して移行を完結させてください。残存ロール保護により旧スコープ解除時も新スコープのラベルは維持されます。※ 旧スコープの解除を忘れると同名ロール重複となり、以降の `handoff` が `worker_role_scope_ambiguous` で確認停止します。

```bash
# 現在のペイン（HERDR_PANE_ID）にローカルロールを紐づける（<pane_id> 省略可）
herdr-orchestrator role set implementer
herdr-orchestrator role set reviewer w15:p2 --label "レビュワー"

# 現在のペインをグローバルロール「調査」として登録し、UI 表示名を指定する
herdr-orchestrator role set 調査 --label "調査 (ログ解析)" --global

# 紐づけ一覧を確認する（--global でグローバル一覧）
herdr-orchestrator role show
herdr-orchestrator role show --global
```

- 対象ペインでエージェントが起動していない場合は `role_target_agent_not_running` で拒否される。
- 旧バージョン（v0.3.1 以前）の `targets` 指紋を持たない Role Binding はサポートされず、`handoff` 実行時に配送前エラー `role_binding_outdated` で停止する（`role show` や `doctor` でも `outdated` として検出される）。再登録（`herdr-orchestrator role set <role>`）を実行すること。
- Role Binding が未設定の標準4ロール（`design`, `reviewer`, `implementer`, `orchestrator`）では、同一 Git worktree 内の Pattern A 命名（`r-<hash16>-<role>`）による解決へフォールバックする。
