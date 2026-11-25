# Herdr Orchestrator `foreground_cwd` 不在時の `cwd` フォールバック検証と `role set` 登録時の人間確認 設計書

- **対象 Issue**: [Issue #194](https://github.com/okinawa-systembit/herdr-orchestration/issues/194)（前段: [Issue #193](https://github.com/okinawa-systembit/herdr-orchestration/issues/193)）
- **ステータス**: 設計中
- **関連文書**:
  - [06-results-and-workflows.md §11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0): Worktree Root 解決（本設計で手順 1 を改訂）
  - [06-results-and-workflows.md §11.1](herdr-orchestrator/06-results-and-workflows.md#sec-11-1): `result-submit` の submit 元照合（本設計で `foreground_cwd` 不在時の扱いを改訂）
  - [06-results-and-workflows.md §11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4): 送信直前再確認と受信側検証
  - [herdr-orchestrator-cross-workspace.md §4.2](herdr-orchestrator-cross-workspace.md#sec-4-2): Role Binding の指紋（`cwd` の取得元）
  - [herdr-orchestrator-cross-workspace.md §7](herdr-orchestrator-cross-workspace.md#sec-7): 受信側の本人確認（`recipient_verification`）
  - [07-skill-and-implementation.md §15.4](herdr-orchestrator/07-skill-and-implementation.md#sec-15-4): 自動許可しない操作

---

<a id="sec-1"></a>

## 1. 背景と目的

<a id="sec-1-1"></a>

### 1.1 背景と課題

Herdr の `foreground_cwd` は、Herdr が **PTY を制御している前景プロセスの cwd を解決できたときだけ**返る項目である。一方 `cwd` は、ペイン / ワークスペースの cwd として常に残る項目である（Herdr 0.9.5 Socket API: 「`foreground_cwd` when Herdr can resolve the cwd of the process currently controlling the pane PTY. The existing `cwd` field remains the pane/workspace cwd …」）。

前景プロセスの cwd が OS 上で読めない場合（例: 前景プロセスが非 dumpable で、同一 uid でも `/proc/<pid>/cwd` が EACCES になる）、そのペインでは `foreground_cwd` が恒常的に欠落する。欠落するかどうかは、**エージェントの種類ではなく、前景プロセスの属性と OS で決まる**。

現行実装は、層によって扱いが違う。

| 層                                                                                                      | `foreground_cwd` が無いとき                                                                 |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 指紋の層（`role set`、`role show` の live 判定、Role Binding 経路の依頼先照合。`herdr pane list` 経由） | `cwd` を使う（`live_agent.py` の `list_live_panes_with_ui`）                                |
| 検証の層（`herdr agent get` 経由の `get_live_agent()` を通る全経路）                                    | `LiveAgentError("live agent foreground_cwd unavailable")` で拒否（`_parse_foreground_cwd`） |

そのため、「登録は成功し live にも見えるのに、prompt 直前の再検証（`assert_handoff_continuable`）で止まり、task が `created` のまま残る」状態になる。止まるのは配送だけではない。配送後の `task-status`（本人確認）・`result-submit`・completion・依頼元の特定も、同じ理由で通らない（[§4](#sec-4)）。

<a id="sec-1-2"></a>

### 1.2 目的

[基本方針正本](core-principles.md) の原則 1（KISS）、原則 2（人間中心の可観測性）、原則 6（単一正本）、指針 8（機械検査とエージェント判断の境界）、指針 10（黙って失敗しない）に基づき、次を実現する。

1. `foreground_cwd` が無いとき、**観測事実だけ**を条件として、検証の層でも `cwd` を使って検証できるようにする。エージェント名・連携の有無・`agent_session` の取得可否は条件にしない。
2. `cwd` で検証してよいペインを、**`role set` で人間が登録パスを確認したペイン**に限る（[§3](#sec-3)）。
3. `cwd` で検証したことを、毎回 stderr と出力 JSON で見えるようにする。
4. 環境変数 1 つで、検証の層を従来どおり厳格（`foreground_cwd` 必須）に一括で戻せるようにする。
5. Task Registry の schema（[§6](herdr-orchestrator/03-task-registry.md#sec-6)）を変更しない。

---

<a id="sec-2"></a>

## 2. 基本設計方針と不採用アプローチ

<a id="sec-2-1"></a>

### 2.1 採用方針

| 設計項目                     | 採用方針                                                                                                                                                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **判定条件**                 | Herdr の応答に `foreground_cwd` が **無い**（キーが無い、または空文字）という観測事実だけで判定する。                                                                                                                                                                                      |
| **取得元の決定（単一関数）** | Herdr の 1 ペイン分の応答から「検証に使う cwd」と `cwd_source` を決める関数を **1 つだけ**定義する（[§3.1](#sec-3-1)）。`herdr agent get` 経路（`_agent_from_herdr_payload`）と `herdr pane list` 経路（`list_live_panes_with_ui`）の両方が、この関数を使う。                              |
| **`cwd_source`**             | `"foreground"`（`foreground_cwd` を使った）または `"pane_cwd"`（`cwd` で代用した）。                                                                                                                                                                                                       |
| **信頼規則（ガードの本体）** | `cwd_source = "pane_cwd"` のペインを当事者（依頼元・依頼先・呼び出し元）として受け入れるのは、**そのペインの Role Binding が存在し、登録 `cwd` が現在の観測 cwd と一致する場合だけ**とする（[§3.3](#sec-3-3)）。Role Binding がない経路（Pattern A）は、この規則により自動的に拒否される。 |
| **既存の照合は維持**         | §11.0 の git root / repository identity の照合、Role Binding 経路の指紋照合（`agent` 一致、かつ `terminal_id` / `agent_session_id` / 1 フォルダ 1 ロールのいずれか）は、変更せずにそのまま効かせる。`agent_session_id` は必須にしない。                                                    |
| **人間確認は登録時に 1 回**  | `role set` で `cwd_source = "pane_cwd"` になる場合、登録パスを明示的に再指定しないと登録しない（[§5](#sec-5)）。配送時には聞かない。                                                                                                                                                       |
| **可観測性**                 | `cwd_source = "pane_cwd"` で検証した場合、毎回 stderr に 1 行警告を出す（1 プロセス・1 ペインにつき 1 回）。`handoff` の出力と `task-status` / `task-result` の `recipient_verification.actual_caller` に `cwd_source` を出す。                                                            |
| **一括で厳格に戻す**         | 環境変数 `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=1` で、検証の層を従来どおり `foreground_cwd` 必須にする（[§7](#sec-7)）。                                                                                                                                                              |
| **Registry schema**          | 変更しない。`cwd_source` は保存せず、毎回の live 観測の結果として出力に出す。Role Binding の保存形式（cross-workspace [§4.2](herdr-orchestrator-cross-workspace.md#sec-4-2)）も変更しない。                                                                                                |

<a id="sec-2-2"></a>

### 2.2 検討したが採用しなかった案

| 不採用案                                                                               | 不採用理由                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 厳格のまま（`foreground_cwd` を返さないペインは常に配送不可）                          | 利用者の設定で直せない環境で、恒常的に使えなくなる。登録は成功するのに配送できない、という層の間の食い違いも残る。                                                                                                                                   |
| `role set` 時のオプトイン + `agent_session_id` を必須にした検証                        | `agent_session` は Herdr の連携が報告する値で、取れるかどうかは連携の有無で決まる。必須にすると、間接的にエージェントの種類に依存する。オプトインの失効管理と Registry の schema 追加が必要で、得られる安全に比べて運用の負担が大きい。              |
| 「`agent_session_id` が取れたら強める」検証の新設                                      | Role Binding 経路の `identity_ok`（`terminal_id` 一致 / `agent_session_id` 一致 / 1 フォルダ 1 ロール）で **すでに行われている**。新しく設計に起こすと、同じ規則が 2 か所にできる（原則 6）。                                                        |
| task ごとの人間承認（配送のたびに yes / no）、`handoff` / `result-submit` 側の `--yes` | Orchestrator CLI は非対話で、依頼元エージェントが実行する。承認フラグはエージェント自身が付けられるため、人間の承認を機械的に保証できない。毎回聞くと形骸化する。依頼先側の検証（`task-status` / `result-submit`）は、そもそも承認の対象にならない。 |
| 前景プロセスの子プロセスの cwd（`herdr pane process-info`）で代用                      | 子プロセス（ツール実行用のシェル等）は自由に `cd` する。Herdr も公式 Issue #3270 / #3696 で、子プロセスへの代替を意図的にやめている。                                                                                                                |
| Role Binding に「確認済み」の印（`cwd_source` 等）を保存し、印がある登録だけを信頼する | 方針 7（`cwd_source` を保存しない）に反し、Role Binding の保存形式の変更と旧形式の移行が必要になる。本設計では「`role set` が成功した = 確認済み」とし、#194 より前に確認なしで登録したものは、移行注記で再登録を求める（[§9.3](#sec-9-3)）。        |
| `role set` で TTY の対話プロンプト（y/N）を出す                                        | `role set` は多くの場合エージェントが非対話で実行するため、入力待ちでツール実行が止まる。人間が直接実行する場合も、エージェントが実行する場合も、同じオプションで確認する方式に統一する（[§5.2](#sec-5-2)）。                                        |

---

<a id="sec-3"></a>

## 3. 判定条件・取得元・信頼規則

<a id="sec-3-1"></a>

### 3.1 取得元を決める単一関数

`live_agent.py` に、次の関数を 1 つだけ定義する（名前は仮）。

```text
resolve_observed_cwd(raw: dict) -> tuple[Path, str]
  1. fg := raw["foreground_cwd"]（str で、空白を除いて空でないとき）
     - あれば: 絶対パスでなければ LiveAgentError、絶対パスなら (Path(fg), "foreground")
  2. 無ければ: pc := raw["cwd"]（同じ条件）
     - あれば: 絶対パスでなければ LiveAgentError、絶対パスなら (Path(pc), "pane_cwd")
  3. どちらも無ければ LiveAgentError("live agent cwd unavailable")
```

- `herdr agent get` 経路（`_agent_from_herdr_payload`）と `herdr pane list` 経路（`list_live_panes_with_ui`）は、どちらもこの関数を使う。いま 2 か所に別々に書かれている判定（`_parse_foreground_cwd` と `live_agent.py:595`）を 1 か所にまとめる（原則 6）。
- `LiveAgentSnapshot` に `cwd_source: str` を追加する（既定値 `"foreground"`）。属性名 `foreground_cwd` は差分を小さくするため変えない。ただし意味は「**検証に使う cwd**（`cwd_source` に従う）」になることを、docstring に明記する。
- `pane list` 経路の pane dict にも `cwd_source` を追加する（#193 で入る項目と同じもの）。

<a id="sec-3-2"></a>

### 3.2 厳格モード（環境変数）の適用点

`HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD` が有効なとき（[§7](#sec-7)）は、次の 2 か所で `cwd_source = "pane_cwd"` を拒否する。それ以外の経路は変えない。

1. `_agent_from_herdr_payload`（検証の層の入口）: `LiveAgentError("live agent foreground_cwd unavailable")` を投げる。これは今の挙動と同じ文言。
2. `role set`: `role_target_foreground_cwd_required` で拒否する（[§5.1](#sec-5-1)）。

`pane list` 経路（指紋の層）は、厳格モードでも今までどおり `cwd` を使う。今の挙動を変えないためで、検証は 1 の入口で止まる。

<a id="sec-3-3"></a>

### 3.3 信頼規則（`pane_cwd` を当事者として受け入れる条件）

`cwd_source = "pane_cwd"` のペインを当事者として受け入れるのは、次の **すべて**を満たす場合だけとする。

| 条件                                                      | 内容                                                                                                                                                                                                                                                                                                                                             |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| R1: Role Binding がある                                   | そのペインの `pane_id` に、該当するスコープ（ローカル / グローバル / `--project` の相手先）の Role Binding がある。依頼先として照合する場合は、そのロールが Registry の `worker.role` と一致すること。                                                                                                                                           |
| R2: 登録 `cwd` と観測 cwd が一致                          | Role Binding に保存された `cwd`（`role set` で人間が確認したパス）と、今の観測 cwd（`resolve()` 後）が一致する。                                                                                                                                                                                                                                 |
| R3: 既存の照合をすべて通る                                | §11.0 の git root / repository identity の照合（同一 Git worktree の task）、または `worker.cwd` との一致（クロスワークスペース・Non-Git の task）。Role Binding 経路では既存の指紋照合（cross-workspace [§5.2](herdr-orchestrator-cross-workspace.md#sec-5-2)）。                                                                               |
| R4: 自ペインで実行する経路では、プロセスの cwd と整合する | 呼び出し元のペインで CLI が動く経路（依頼元の特定、`task-status` / `task-result`、`result-submit`）では、CLI プロセスの cwd が観測 cwd の配下にあること（`is_strictly_within_directory(proc_cwd, observed_cwd)`）。今の高速経路にある「プロセスが task の worktree 外で動いていれば snapshot を信頼する」という例外は、`pane_cwd` では使わない。 |

- R1 と R2 により、「`role set` で人間が確認したパス」以外の `cwd` は、検証に使われない。
- R4 は、Issue #194 のガード一覧にない **追加のガード**である。自ペインで動く経路では、OS から読めるプロセス自身の cwd という独立した観測がある。そのため、「登録時にすでに `cwd` がずれていた」場合も、**依頼先が受信確認（`task-status`）をした時点で**食い違いとして止められる（[§8.1](#sec-8-1)）。拒否を増やすだけで、受け入れを広げることはない。
- `cwd_source = "foreground"` のときは、R1〜R4 を追加では要求しない（今までと同じ）。

---

<a id="sec-4"></a>

## 4. 経路ごとの扱い

| 経路                                                                            | 観測の取得                                 | `pane_cwd` のときの扱い（既定）                                                                                                                                                            | 厳格モード                                                     | 拒否時のエラー / 理由                                                                                                     |
| ------------------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `role set`                                                                      | `pane list`（`inspect_pane_for_role_set`） | 登録パスの再指定が必要（[§5](#sec-5)）                                                                                                                                                     | 拒否                                                           | `role_target_cwd_confirmation_required` / `role_target_cwd_confirmation_mismatch` / `role_target_foreground_cwd_required` |
| `role show`                                                                     | `pane list`                                | `cwd_source` を表示する（#193）。判定は今のまま                                                                                                                                            | 同左（表示だけ）                                               | —                                                                                                                         |
| `handoff`: 依頼元の特定（`get_caller_live_agent`）                              | `agent get`                                | R1（依頼元ペインのローカル / グローバルの Role Binding）、R2、R4 を満たせば受け入れる                                                                                                      | 拒否（今と同じ）                                               | `requester_context_unverified`（`details.reason = "pane_cwd_unconfirmed"`）                                               |
| `handoff`: 依頼先（Role Binding 経路・`--project` 経路）                        | `pane list`                                | 既存の指紋照合（`cwd_ok` 等）で判定（今と同じ）                                                                                                                                            | 今と同じ（下の行で止まる）                                     | 既存の `worker_context_changed` 等                                                                                        |
| `handoff`: 依頼先（Pattern A、`verify_live_agent_context`）                     | `agent get`                                | **拒否**（R1 を満たさない）                                                                                                                                                                | 拒否                                                           | `worker_unavailable`（`details.reason = "foreground_cwd_required"`）                                                      |
| `handoff`: prompt 直前の再検証（`assert_handoff_continuable`）                  | `agent get`                                | R3 で判定する。依頼先は同じプロセスの中で、Role Binding 経路で選定済み（R1・R2 は選定時に確認済み）                                                                                        | 拒否（今と同じ）                                               | `handoff_not_continuable`                                                                                                 |
| sync: 結果待ちの間の監視（`_observe_worker_during_result_wait`）                | `agent get`                                | 状態（`blocked` 等）の観測だけに使う。cwd では判定しない                                                                                                                                   | 拒否（今と同じ）                                               | `worker_unavailable`                                                                                                      |
| `task-status` / `task-result`: 本人確認（`resolve_caller_live_agent_for_task`） | `agent get`                                | R1（依頼先として照合する場合は `worker.role` を含む Role Binding）、R2、R3、R4 を満たせば `verified = true`。今の「Role Binding が無ければ role_ok とする」緩和は、`pane_cwd` では使わない | `verified = false`（今と同じ `caller_live_agent_unavailable`） | 既定: `reason = "caller_cwd_unconfirmed"`（新設）                                                                         |
| `result-submit`（`resolve_caller_live_agent_for_worker`）                       | `agent get`                                | `task-status` の依頼先照合と同じ条件                                                                                                                                                       | 拒否（今と同じ）                                               | `worker_context_unverified`                                                                                               |
| 呼び出し元の自己回復（#121、`_try_recover_unique_party`）                       | `agent get` / `pane list`                  | 候補が `pane_cwd` の場合は回復しない（fail closed）。環境変数が欠落・汚染された状態で、確認されていない cwd を使って呼び出し元を推定しない                                                 | 同左                                                           | 既存の `caller_live_agent_unavailable` 等                                                                                 |
| completion: 依頼元の再開（`completion_commands.py` の `verify_live_party_cwd`） | `agent get`                                | R3 で判定する。依頼元は handoff 時に R1・R2・R4 を確認済み                                                                                                                                 | 拒否（今と同じ）                                               | 既存の completion 失敗理由                                                                                                |

- 「拒否（今と同じ）」は、厳格モードの挙動が現行実装と同じであることを意味する。
- 新しい理由コードは `caller_cwd_unconfirmed` の 1 つだけとする。他は既存のエラーコードに `details.reason` を足して表す（エラーコードの一覧を増やさない）。
- エラーコード・`details.reason`・警告のキーには、エージェント名を含めない。

---

<a id="sec-5"></a>

## 5. `role set` 登録時の人間確認

<a id="sec-5-1"></a>

### 5.1 挙動

`role set` に、新しいオプション `--confirm-pane-cwd <absolute_path>` を追加する（名前は仮）。

| 観測                        | `--confirm-pane-cwd`         | 結果                                                                                                                                                                                                                                                            |
| --------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cwd_source = "foreground"` | なし                         | 今までどおり登録する。                                                                                                                                                                                                                                          |
| `cwd_source = "foreground"` | あり                         | 登録する。オプションは使わない旨を stderr に 1 行出す（黙って無視しない）。                                                                                                                                                                                     |
| `cwd_source = "pane_cwd"`   | なし                         | **登録しない**。終了コード 1、`role_target_cwd_confirmation_required`。`details` に `pane_id`・`observed_cwd`・`cwd_source`・`display_summary`、および再実行用のコマンド例を含める。Role Binding ファイルは変更せず、UI ラベルの同期（`pane rename`）もしない。 |
| `cwd_source = "pane_cwd"`   | あり（`resolve()` 後に一致） | 登録する。出力 JSON に `cwd_source: "pane_cwd"` と `cwd_confirmed: true` を含め、stderr に警告を出す。                                                                                                                                                          |
| `cwd_source = "pane_cwd"`   | あり（不一致）               | **登録しない**。終了コード 1、`role_target_cwd_confirmation_mismatch`（`details` に、指定されたパスと観測 cwd の両方）。                                                                                                                                        |
| 厳格モード + `pane_cwd`     | どちらでも                   | **登録しない**。終了コード 1、`role_target_foreground_cwd_required`。登録できても配送できないという食い違いを、登録の時点で止めるため。                                                                                                                         |

- **承認 = 登録**: `role set` が成功し、Role Binding に `cwd` が保存されたことが、承認の記録そのものになる。別の承認ファイルは作らない。
- **拒否は保存しない**: 人間が拒否した場合は、オプションを付けずに終わるだけである。何も保存しないので、次に `role set` を実行したときは改めて確認になる（自動拒否にならない）。
- **確認するパスの値を指定させる理由**: 値の一致を求めることで、「確認したパス」と「登録するパス」の間で観測が変わった場合（確認から再実行までの間にペインの状態が変わった場合）に登録を止められる。`--yes` のような真偽値のフラグでは、これができない。
- 暗黙のペイン（`HERDR_PANE_ID`）で自ペインを登録する場合の既存チェック（`env_pane_cwd_mismatch`: 観測 cwd とプロセスの cwd の整合）は、そのまま効く。

<a id="sec-5-2"></a>

### 5.2 非対話実行の扱い

- `role set` は対話プロンプトを出さない。人間が端末で直接実行する場合も、エージェントが実行する場合も、`--confirm-pane-cwd` の有無だけで決まる。
- エージェントが `role_target_cwd_confirmation_required` を受け取った場合の手順（references に記載。[§6](#sec-6)）:
  1. `details.display_summary` と `details.observed_cwd` を人間に示す。
  2. 「このパスがそのペインの実際の作業場所か」を確認してもらう。
  3. 人間が明示的に OK した場合に限り、`--confirm-pane-cwd <observed_cwd>` を付けて再実行する。
- エージェントが自分で `--confirm-pane-cwd` を付けて再実行することは、機械的には防げない（指針 8: ここは機械では止められない部分）。補強として、`role set --confirm-pane-cwd` を **自動許可の対象にしない**ことを §15.4 に明記する。そうすれば、ホスト側エージェントがコマンド承認を出す環境では、人間の目が入る。
- `handoff` / `result-submit` 側には、承認を飛ばすオプションを作らない。

---

<a id="sec-6"></a>

## 6. 可観測性と人間向けの報告

<a id="sec-6-1"></a>

### 6.1 stderr 警告

`cwd_source = "pane_cwd"` の観測を検証に使った CLI 実行では、次の 1 行を stderr に出す。

```text
[herdr-orchestrator] warning: foreground_cwd unavailable for pane <pane_id>; verified with pane cwd <path> (cwd_source=pane_cwd).
```

- 1 プロセスの中で、同じペインにつき 1 回だけ出す。sync の結果待ちのように同じペインを何度も観測する場合も、1 回にする。
- 標準出力の JSON の形は変えない（[§6.2](#sec-6-2) の項目追加だけ）。

<a id="sec-6-2"></a>

### 6.2 出力 JSON

| コマンド                      | 追加項目                                                                                                                                                                                                                                                               |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handoff`                     | `cwd_sources: {"requester": "foreground" \| "pane_cwd", "worker": "foreground" \| "pane_cwd"}`。`pane_cwd` が 1 つでもあれば、既存の `warnings` 配列に `{"code": "foreground_cwd_unavailable", "party": "requester" \| "worker", "pane_id": ..., "cwd": ...}` を足す。 |
| `task-status` / `task-result` | `recipient_verification.actual_caller.cwd_source`                                                                                                                                                                                                                      |
| `role set`                    | `cwd_source`、`cwd_confirmed`（`pane_cwd` で登録したときだけ `true`）                                                                                                                                                                                                  |
| `role show`                   | 各ロールの live 観測に `cwd_source`（#193 で入るもの。本設計では変えない）                                                                                                                                                                                             |

<a id="sec-6-3"></a>

### 6.3 人間向けチャット報告（references に記載）

- **依頼元**（`handoff` を実行したエージェント）: `handoff` の出力の `warnings` に `foreground_cwd_unavailable` がある場合、**同じ会話の中で初回だけ**、人間に次を報告する。
  - 「`<display_summary>` は `foreground_cwd` が取れないため、`role set` で確認したパス（`<cwd>`）で検証して配送した」
  - 2 回目以降は報告しない（毎回はうるさいため）。
  - 記載先: `skills/herdr-orchestrator/references/handoff.md`（委託後の手順）。
- **依頼先**: stderr の警告だけとし、チャットでは報告しない。この警告だけを理由に作業を止めず、`recipient_verification.verified` の判定に従う。記載先: `skills/herdr-orchestrator/references/worker-receive.md`（1 行）。
- **`role set` の確認手順**（[§5.2](#sec-5-2)）: 記載先は `skills/herdr-orchestrator/references/roles.md`。
- いずれも、エージェント名を条件に書かない。主語は「`foreground_cwd` を返さないペイン」とする。

---

<a id="sec-7"></a>

## 7. 一括して厳格に戻す環境変数

| 項目         | 仕様                                                                                                                                                                |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 名前         | `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD`                                                                                                                         |
| 値           | `1` = 厳格。未設定・空文字・`0` = 既定（フォールバックあり）。                                                                                                      |
| 不正な値     | それ以外の値は、stderr に警告を出し、**厳格として扱う**（安全側）。                                                                                                 |
| 効く範囲     | [§3.2](#sec-3-2) の 2 か所だけ。結果として、検証の層のすべての経路が現行と同じ挙動になり、`role set` は `pane_cwd` の登録を拒否する。                               |
| 読み取り     | `dispatch_commands.py` の `stale_*_threshold_sec()` と同じく、関数 1 つ（例: `require_foreground_cwd() -> bool`）で読む。呼び出しのたびに読む（キャッシュしない）。 |
| 既存との違い | `HERDR_ORCHESTRATOR_STALE_*` は不正な値を既定値に戻すが、本変数は安全上の切り替えなので、不正な値を **厳格**として扱う。この違いは public-cli.md に明記する。       |

---

<a id="sec-8"></a>

## 8. 許容するリスクと段階的な引き締め

<a id="sec-8-1"></a>

### 8.1 リスクの整理

| 場合                                                                     | 検知                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 登録後に状態が変わる（エージェントの入れ替え、別の場所での起動し直し等） | 既存の指紋照合と R2（登録 `cwd` との一致）で止まる。                                                                                                                                                                                                              |
| 登録時に `cwd` がすでに実際の作業場所とずれている                        | 登録と照合が同じ値を見るため、**配送前には検知できない**。依頼先が `task-status` を実行した時点で、R4（プロセスの cwd との整合）により `verified = false` で止まる見込みが高い。ただし、依頼先のツール実行用シェルの cwd も同じようにずれている場合は止まらない。 |
| ずれた先が別のリポジトリ                                                 | R3（git root / repository identity）で止まる。                                                                                                                                                                                                                    |
| セッションの途中で、前景プロセス自身が作業ディレクトリを変える           | `cwd` が追随するかは未確認（[§10.3](#sec-10-3) の実測項目）。R4 で止まる場合がある。                                                                                                                                                                              |

**許容する範囲**: 機械で止められずに残るのは、「同じリポジトリの中で、登録時の確認をすり抜けたずれ」だけである。この場合の誤配送は、依頼先の作業場所が同じリポジトリ内でずれる程度に限られる。警告・出力・チャット報告によって、人間が目視で気づける。本設計はこれを許容する（Issue #194「許容するリスク」）。

<a id="sec-8-2"></a>

### 8.2 段階的な引き締め

1. **段階 0（既定）**: 本設計のとおり（フォールバックあり、R1〜R4）。
2. **段階 1（運用で即時）**: 誤配送が見つかった環境では、`HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=1` で現行の厳格な挙動に戻す。コードの変更は要らない。
3. **段階 2（設計変更）**: 実測（[§10.3](#sec-10-3)）で `cwd` が古いまま残ることが分かった場合は、既定を反転させる（未設定 = 厳格、明示的に有効にしたときだけフォールバック）。この判断は、別 Issue で設計書を改定して行う。

観測した事例は [§18 実機記録](herdr-orchestrator-verification-log.md) に、観測例として残す（エージェント名は観測例としてだけ書いてよい）。

---

<a id="sec-9"></a>

## 9. 既存設計への影響

<a id="sec-9-1"></a>

### 9.1 Task Registry / Role Binding の保存形式

- Task Registry の schema（[§6](herdr-orchestrator/03-task-registry.md#sec-6)）: **変更なし**。`worker.cwd` には、今までどおり選定時の観測 cwd（`cwd_source` によらない）を保存する。
- Role Binding の保存形式（cross-workspace [§4.2](herdr-orchestrator-cross-workspace.md#sec-4-2)）: **変更なし**。`cwd` の取得元の説明に、`cwd_source` と確認手順へのリンクを足すだけにする。

<a id="sec-9-2"></a>

### 9.2 改訂する文書

| 文書                                                                                               | 改訂内容                                                                                                                                                                                                    | 時期           |
| -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| [06-results-and-workflows.md §11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0)       | 手順 1 を「[§3.1](#sec-3-1) の関数で検証用の cwd と `cwd_source` を決める」に改訂。「`foreground_cwd` を worktree root の代用にしない」は、「検証用の cwd を worktree root の代用にしない」として維持する。 | 本設計 PR      |
| [06-results-and-workflows.md §11.1](herdr-orchestrator/06-results-and-workflows.md#sec-11-1)       | 「Herdr が `foreground_cwd` を返さない場合 … 拒否」を、[§3.3](#sec-3-3) の信頼規則と厳格モードへのリンクに置き換える。                                                                                      | 本設計 PR      |
| [06-results-and-workflows.md §11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4)       | 送信直前の再確認・受信側の検証が [§4](#sec-4) に従うことを 1 行追加する。                                                                                                                                   | 本設計 PR      |
| [cross-workspace §4.2](herdr-orchestrator-cross-workspace.md#sec-4-2)                              | `cwd` の行に、`cwd_source` と [§5](#sec-5) の確認手順を追記する。                                                                                                                                           | 本設計 PR      |
| [cross-workspace §7.4](herdr-orchestrator-cross-workspace.md#sec-7-4)                              | `actual_caller.cwd_source` と、理由コード `caller_cwd_unconfirmed` を追記する。                                                                                                                             | 本設計 PR      |
| [07-skill-and-implementation.md §15.4](herdr-orchestrator/07-skill-and-implementation.md#sec-15-4) | `role set --confirm-pane-cwd` を自動許可しない操作に追加する。                                                                                                                                              | 本設計 PR      |
| `skills/herdr-orchestrator/references/public-cli.md`                                               | `--confirm-pane-cwd`、出力項目（[§6.2](#sec-6-2)）、警告、環境変数、新しいエラーコード。                                                                                                                    | 実装 PR（D-2） |
| `references/roles.md` / `handoff.md` / `worker-receive.md`                                         | [§6.3](#sec-6-3) の手順。#193 で入る暫定運用（「配送できない」）の記述を、本設計の挙動に置き換える。                                                                                                        | 実装 PR        |

<a id="sec-9-3"></a>

### 9.3 移行注記

- 本設計の実装より前に、`foreground_cwd` が無いペインを `role set` で登録していた場合、その登録は人間の確認を経ていない（今の実装は黙って `cwd` で登録する）。
- 実装後も R1・R2 を満たすので、そのまま配送に使われる。ただし、毎回の警告と出力で `cwd_source = "pane_cwd"` が見えるので、人間は気づける。
- references（`roles.md`）に、「`cwd_source = pane_cwd` と表示されるロールは、`role set --confirm-pane-cwd` で登録し直して、パスを確認すること」を記載する。
- 古い登録を機械的に見分けるには、Role Binding に印を保存する必要がある。これは方針 7 に反するので採らない（[§2.2](#sec-2-2)）。

---

<a id="sec-10"></a>

## 10. 実装・検証計画

<a id="sec-10-1"></a>

### 10.1 #193 との境界と実装順

- **#193（前段）**: 挙動を変えない可視化。`role set` の警告と `cwd_source` の出力、`role show` の `cwd_source`、暫定運用の docs。
- **本設計（#194）**: 検証の層の挙動変更（[§3](#sec-3)・[§4](#sec-4)）、`role set` の確認（[§5](#sec-5)）、環境変数（[§7](#sec-7)）。
- 実装は **#193 のマージ後**に、`main` から行う。#193 で入る `cwd_source` の項目名・値は、本設計と同じもの（`"foreground"` / `"pane_cwd"`）を前提にする。食い違う場合は、本設計書を改定してから実装する。

<a id="sec-10-2"></a>

### 10.2 段階的な実装手順

1. `resolve_observed_cwd()` と `LiveAgentSnapshot.cwd_source` を追加し、`agent get` / `pane list` の両経路をこの関数に寄せる。挙動は変えない（検証の層は引き続き `pane_cwd` を拒否する）。
2. `require_foreground_cwd()` と、[§3.2](#sec-3-2) の適用点を追加する。
3. [§3.3](#sec-3-3) の信頼規則を、[§4](#sec-4) の各経路に入れる。検証の層の入口での一律拒否は、ここで外す。
4. `role set --confirm-pane-cwd`（[§5](#sec-5)）。
5. 警告と出力 JSON（[§6.1](#sec-6-1)・[§6.2](#sec-6-2)）。
6. references と public-cli.md（[§6.3](#sec-6-3)、[§9.2](#sec-9-2)）。

<a id="sec-10-3"></a>

### 10.3 実機での確認項目（設計承認後、実装前後に実施）

`foreground_cwd` が欠落するペインについて、次を観測項目として記録する。エージェント名は観測例としてだけ書く。

1. `herdr agent get <pane_id>` に `cwd` / `terminal_id` / `agent_session` があるか。`cwd` は、エージェントを起動したディレクトリと一致するか。
2. エージェントを終了し、同じペインで別のディレクトリへ `cd` してから起動し直したとき、`cwd` が新しいディレクトリに変わるか（[§8.2](#sec-8-2) の段階 2 を判断する材料）。
3. 前景プロセスの中から作業ディレクトリを変えたとき、`cwd` が追随するか。
4. 依頼先として `task-status` を実行したとき、R4 が誤って拒否しないか（ツール実行用のシェルの cwd が、観測 cwd の配下にあるか）。

<a id="sec-10-4"></a>

### 10.4 テスト計画（FC-01〜FC-16）

fake herdr の応答から `foreground_cwd` を省き、**CLI の実際の終了コード・標準出力 JSON・標準エラー・Registry / Role Binding ファイルの内容**で確認する（指針 10。引数の変換だけの検証にしない）。

| ID        | 観点                                  | 前提・操作                                                                                                                         | 期待値                                                                                                                                                                                                                                                                                                    |
| --------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **FC-01** | 取得元の決定                          | `resolve_observed_cwd` に (a) `foreground_cwd` あり、(b) `foreground_cwd` なし・`cwd` あり、(c) どちらもなし、(d) `cwd` が相対パス | (a) `"foreground"`、(b) `"pane_cwd"` と `cwd` の値、(c)(d) `LiveAgentError`。`agent get` 経路と `pane list` 経路の結果が一致すること                                                                                                                                                                      |
| **FC-02** | `role set` 確認なし                   | `pane_cwd` のペインに、`--confirm-pane-cwd` なしで `role set`                                                                      | 終了コード 1、`role_target_cwd_confirmation_required`、`details.observed_cwd` / `cwd_source` あり。Role Binding ファイルが実行前と同じ内容。`herdr pane rename` が呼ばれていない                                                                                                                          |
| **FC-03** | `role set` 確認あり（一致）           | FC-02 の後、`--confirm-pane-cwd <observed_cwd>` で再実行                                                                           | 終了コード 0、出力に `cwd_source: "pane_cwd"`・`cwd_confirmed: true`。stderr に警告。Role Binding ファイルに `cwd` が保存されている                                                                                                                                                                       |
| **FC-04** | `role set` 確認あり（不一致）         | 別のパスを `--confirm-pane-cwd` に指定                                                                                             | 終了コード 1、`role_target_cwd_confirmation_mismatch`。Role Binding ファイルが変わっていない                                                                                                                                                                                                              |
| **FC-05** | 拒否は保存しない                      | FC-02 → FC-04 → FC-03 の順に実行                                                                                                   | FC-03 が成功する（前の拒否・不一致が残っていない）。Role Binding 以外の保存ファイルが増えていない                                                                                                                                                                                                         |
| **FC-06** | Role Binding 経路の全工程（async）    | 依頼先ペインが `pane_cwd`、FC-03 で登録済み。依頼元が `handoff --mode async`、依頼先が `task-status` → `result-submit`             | `handoff`: `prompt_sent = true`、`cwd_sources.worker = "pane_cwd"`、`warnings` に `foreground_cwd_unavailable`。`task-status`: `verified = true`、`actual_caller.cwd_source = "pane_cwd"`。`result-submit` 成功。Registry の `result.status = succeeded`                                                  |
| **FC-07** | Role Binding 経路の全工程（sync）     | FC-06 と同じ構成で `--mode sync`                                                                                                   | 結果待ちが完了し、`result_wait.completed = true`。stderr の警告は、依頼先ペインについて 1 回だけ                                                                                                                                                                                                          |
| **FC-08** | Pattern A は拒否                      | Role Binding なしの標準ロールで、依頼先ペインが `pane_cwd`                                                                         | `worker_unavailable`、`details.reason = "foreground_cwd_required"`。Registry に task が作られていない。prompt が送られていない                                                                                                                                                                            |
| **FC-09** | 厳格モードで全経路が拒否              | `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=1` で、FC-03・FC-06 と同じ操作                                                          | `role set` は `role_target_foreground_cwd_required` で、Role Binding は変わらない。`handoff` は `handoff_not_continuable` で、prompt は送られない。既存の task に対する `task-status` は `verified = false`（`reason = "caller_live_agent_unavailable"`）、`result-submit` は `worker_context_unverified` |
| **FC-10** | 不正な環境変数値                      | `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=yes`                                                                                    | stderr に警告が出て、FC-09 と同じく厳格になる                                                                                                                                                                                                                                                             |
| **FC-11** | 別リポジトリの `cwd`                  | リポジトリ A で登録した後、依頼先ペインの `cwd` がリポジトリ B を返す                                                              | `handoff` は `worker_context_changed` で、prompt は送られない。B 側で実行した `result-submit` は `worker_context_unverified`                                                                                                                                                                              |
| **FC-12** | 依頼元の `pane_cwd`                   | 依頼元ペインが `pane_cwd`、(a) Role Binding なし、(b) 登録済みで `cwd` 一致、(c) 登録済みで CLI プロセスの cwd が観測 cwd の外     | (a)(c) `requester_context_unverified`（`details.reason = "pane_cwd_unconfirmed"`）で、task は作られない。(b) 成功し、`cwd_sources.requester = "pane_cwd"`                                                                                                                                                 |
| **FC-13** | 受信確認でのプロセス cwd の整合（R4） | 依頼先ペインが `pane_cwd`。CLI プロセスの cwd を観測 cwd の外にして `task-status`                                                  | `verified = false`、`reason = "caller_cwd_unconfirmed"`                                                                                                                                                                                                                                                   |
| **FC-14** | 受信確認での Role Binding 必須（R1）  | 依頼先ペインが `pane_cwd`。task の作成後に、依頼先のロールを `role clear`                                                          | `task-status` は `verified = false`（`caller_cwd_unconfirmed`）、`result-submit` は `worker_context_unverified`                                                                                                                                                                                           |
| **FC-15** | completion（依頼元が `pane_cwd`）     | FC-12(b) の依頼元から async handoff。依頼先が `result-submit`（依頼元は idle）                                                     | `completion.status = sent`。依頼元への prompt が送られている                                                                                                                                                                                                                                              |
| **FC-16** | 回帰（`foreground_cwd` あり）         | 全ペインで `foreground_cwd` を返す fake herdr で、FC-06 と同じ操作                                                                 | stderr に `foreground_cwd unavailable` 警告が出ない。`cwd_sources` はすべて `"foreground"`。既存のテストがすべて通る                                                                                                                                                                                      |

- 合わせて、`npm run test:orchestrator`、`npm run verify:orchestrator`、`npm run format:md && npm run check:md` を通す。
- 実装の差分に、エージェント名を条件にした分岐や文言が入っていないことを、レビューで確認する（機械検査は作らない。指針 8）。
