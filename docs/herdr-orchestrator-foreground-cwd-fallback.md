# Herdr Orchestrator `foreground_cwd` 不在時の `cwd` フォールバック検証 設計書

- **対象 Issue**: [Issue #194](https://github.com/okinawa-systembit/herdr-orchestration/issues/194)（前段: [Issue #193](https://github.com/okinawa-systembit/herdr-orchestration/issues/193)）
- **ステータス**: 実装中
- **関連文書**:
  - [06-results-and-workflows.md §11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0): Worktree Root 解決（本設計で手順 1 を改訂）
  - [06-results-and-workflows.md §11.1](herdr-orchestrator/06-results-and-workflows.md#sec-11-1): `result-submit` の submit 元照合（本設計で `foreground_cwd` 不在時の扱いを改訂）
  - [06-results-and-workflows.md §11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4): 送信直前再確認と受信側検証
  - [herdr-orchestrator-cross-workspace.md §4.2](herdr-orchestrator-cross-workspace.md#sec-4-2): Role Binding の指紋（`cwd` の取得元）
  - [herdr-orchestrator-cross-workspace.md §7](herdr-orchestrator-cross-workspace.md#sec-7): 受信側の本人確認（`recipient_verification`）

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

[基本方針正本](core-principles.md) の原則 1（KISS）、原則 2（人間中心の可観測性）、原則 6（単一正本）、指針 10（黙って失敗しない）に基づき、次を実現する。

1. `foreground_cwd` が無いとき、**観測事実だけ**を条件として、検証の層でも pane の `cwd` で代用できるようにする。エージェント名・連携の有無・`agent_session` の取得可否は条件にしない。
2. 代用したことを、毎回 stderr と出力 JSON で人間に見えるようにする。
3. 新しい厳格なチェックは増やさない。照合は既存の仕組み（§11.0 の git root / repository identity、Role Binding 経路の指紋照合）をそのまま使う。
4. 環境変数 1 つで、検証の層を従来どおり厳格（`foreground_cwd` 必須）に一括で戻せるようにする。
5. Task Registry の schema（[§6](herdr-orchestrator/03-task-registry.md#sec-6)）と Role Binding の保存形式を変更しない。

---

<a id="sec-2"></a>

## 2. 基本設計方針と不採用アプローチ

<a id="sec-2-1"></a>

### 2.1 採用方針

| 設計項目                     | 採用方針                                                                                                                                                                                                                                                                            |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **判定条件**                 | Herdr の応答に `foreground_cwd` が **無い**（キーが無い、または空文字）という観測事実だけで判定する。                                                                                                                                                                               |
| **取得元の決定（単一関数）** | Herdr の 1 ペイン分の応答から「検証に使う cwd」と `cwd_source` を決める関数を **1 つだけ**定義する（[§3.1](#sec-3-1)）。`herdr agent get` 経路（`_agent_from_herdr_payload`）と `herdr pane list` 経路（`list_live_panes_with_ui`）の両方が、この関数を使う。                       |
| **`cwd_source`**             | `"foreground"`（`foreground_cwd` を使った）または `"pane_cwd"`（`cwd` で代用した）。                                                                                                                                                                                                |
| **照合は既存のまま**         | 代用した cwd は、`foreground_cwd` と同じように既存の照合に通す。§11.0 の git root / repository identity の照合、`worker.cwd` との一致、Role Binding 経路の指紋照合（`agent` 一致、かつ `terminal_id` / `agent_session_id` / 1 フォルダ 1 ロールのいずれか）。新しい条件は足さない。 |
| **Pattern A は厳格**         | Role Binding がない Pattern A の依頼先選定だけは、`cwd_source = "pane_cwd"` を拒否する（人間がペインを指定して登録していない経路では、代用した cwd を使わない）。                                                                                                                   |
| **可観測性**                 | `cwd_source = "pane_cwd"` で検証した場合、毎回 stderr に 1 行警告を出す。`handoff` の出力と `task-status` / `task-result` の `recipient_verification.actual_caller` に `cwd_source` を出す。依頼元は、同じ会話の中で初回だけ人間にチャットで報告する。                              |
| **一括で厳格に戻す**         | 環境変数 `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=1` で、検証の層を従来どおり `foreground_cwd` 必須にする（[§6](#sec-6)）。                                                                                                                                                       |
| **保存形式**                 | 変更しない。`cwd_source` は保存せず、毎回の live 観測の結果として出力に出す。                                                                                                                                                                                                       |
| **許容するリスク**           | 代用した `cwd` が実際の作業場所とずれていても、その `cwd` 自体が期待どおりのリポジトリを指していれば、機械では検知しない（古い `cwd` が残り、実際は別のリポジトリで動いている場合を含む）。誤配送は警告と出力から人間が目視で気づけるため、ある程度は許容する（[§7](#sec-7)）。     |

<a id="sec-2-2"></a>

### 2.2 検討したが採用しなかった案

| 不採用案                                                                                                                              | 不採用理由                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 厳格のまま（`foreground_cwd` を返さないペインは常に配送不可）                                                                         | 利用者の設定で直せない環境で、恒常的に使えなくなる。登録は成功するのに配送できない、という層の間の食い違いも残る。                                                                                                                                                                                                                                                        |
| `role set` 時の登録パスの人間確認（確認オプションを付けないと登録しない方式）                                                         | 厳格なチェックを増やしたくない、というプロジェクトの判断により採らない。誤配送は警告・出力・チャット報告から人間が目視で気づけるため、ある程度は許容する。`role set` の警告と `cwd_source` の表示は #193 で入る（[§5](#sec-5)）。                                                                                                                                         |
| `pane_cwd` のときだけ追加の条件を課す（Role Binding の登録 `cwd` との一致、CLI プロセスの cwd との整合、completion での指紋照合など） | 同上。照合は既存の仕組みに任せ、`foreground` と `pane_cwd` で検証の条件を変えない（Pattern A の依頼先選定を除く）。条件が経路ごとに分かれると、仕様と実装の両方が複雑になる（原則 1）。なお、CLI プロセスの cwd との整合は、古い `cwd` が残る場合（[§7.1](#sec-7-1) の 3 行目）の検知に有効なので、[§7.2](#sec-7-2) の段階 2 で追加の照合を検討するときの第一候補とする。 |
| `role set` 時のオプトイン + `agent_session_id` を必須にした検証                                                                       | `agent_session` は Herdr の連携が報告する値で、取れるかどうかは連携の有無で決まる。必須にすると、間接的にエージェントの種類に依存する。                                                                                                                                                                                                                                   |
| task ごとの人間承認（配送のたびに yes / no）、`handoff` / `result-submit` 側の `--yes`                                                | Orchestrator CLI は非対話で、依頼元エージェントが実行する。承認フラグはエージェント自身が付けられるため、人間の承認を機械的に保証できず、毎回聞くと形骸化する。                                                                                                                                                                                                           |
| 前景プロセスの子プロセスの cwd（`herdr pane process-info`）で代用                                                                     | 子プロセス（ツール実行用のシェル等）は自由に `cd` する。Herdr も公式 Issue #3270 / #3696 で、子プロセスへの代替を意図的にやめている。                                                                                                                                                                                                                                     |

---

<a id="sec-3"></a>

## 3. 判定条件と取得元

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

- `herdr agent get` 経路（`_agent_from_herdr_payload`）と `herdr pane list` 経路（`list_live_panes_with_ui`）は、どちらもこの関数を使う。いま 2 か所に別々に書かれている判定（`_parse_foreground_cwd` と `list_live_panes_with_ui` の中のフォールバック）を 1 か所にまとめる（原則 6）。#193 で `pane list` 経路に `cwd_source` を出す処理が入っている場合も、この関数に寄せる。
- `LiveAgentSnapshot` に `cwd_source: str` を追加する（既定値 `"foreground"`）。属性名 `foreground_cwd` は差分を小さくするため変えない。ただし意味は「**検証に使う cwd**（`cwd_source` に従う）」になることを、docstring に明記する。

<a id="sec-3-2"></a>

### 3.2 厳格モード（環境変数）の適用点

`HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD` が有効なとき（[§6](#sec-6)）は、次の 2 か所だけで `cwd_source = "pane_cwd"` を拒否する。

1. `_agent_from_herdr_payload`（検証の層の入口）: `LiveAgentError("live agent foreground_cwd unavailable")` を投げる。これは今の挙動と同じ文言。
2. `role set`: `role_target_foreground_cwd_required` で拒否し、Role Binding を保存しない。登録できても配送できないという食い違いを、登録の時点で止めるため。

`pane list` 経路の観測そのもの（`role show` の live 判定、Role Binding 経路の依頼先照合）は、厳格モードでも今までどおり `cwd` を使う。そのため、厳格モードでは今と同じく、prompt 直前の再検証（`assert_handoff_continuable`）で止まり、task が `created` のまま残る場合がある。この task は、既存の手順どおり `reconcile-dispatch` で終端化する（[§6.12](herdr-orchestrator/03-task-registry.md#sec-6-12)）。厳格モードは現行の挙動に戻す切り替えなので、この点に追加の対処はしない。

---

<a id="sec-4"></a>

## 4. 経路ごとの扱い

| 経路                                                                            | 観測の取得                | `pane_cwd` のときの扱い（既定）                                                 | 厳格モード                                                     |
| ------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `role set`                                                                      | `pane list`               | 代用した `cwd` で登録する（今と同じ）。警告と `cwd_source` の出力は #193 で入る | 拒否（[§3.2](#sec-3-2) の 2）                                  |
| `role show`                                                                     | `pane list`               | 今と同じ。`cwd_source` の表示は #193 で入る                                     | 今と同じ                                                       |
| `handoff`: 依頼元の特定（`get_caller_live_agent`）                              | `agent get`               | 代用した cwd で、既存の手順のまま worktree / identity を求める                  | 拒否（今と同じ）                                               |
| `handoff`: 依頼先（Role Binding 経路・`--project` 経路）                        | `pane list`               | 既存の指紋照合（`cwd_ok` 等）で判定する（今と同じ）                             | 今と同じ（次の再検証で止まる）                                 |
| `handoff`: 依頼先（Pattern A、`verify_live_agent_context`）                     | `agent get`               | **拒否**（`worker_unavailable`、`details.reason = "foreground_cwd_required"`）  | 拒否                                                           |
| `handoff`: prompt 直前の再検証（`assert_handoff_continuable`）                  | `agent get`               | 代用した cwd で、既存の照合（§11.0 または `worker.cwd` との一致）を行う         | 拒否（今と同じ）                                               |
| sync: 結果待ちの間の監視（`_observe_worker_during_result_wait`）                | `agent get`               | 状態（`blocked` 等）の観測に使う（今と同じ）                                    | 拒否（今と同じ）                                               |
| `task-status` / `task-result`: 本人確認（`resolve_caller_live_agent_for_task`） | `agent get`               | 代用した cwd で、既存の照合を行う                                               | `verified = false`（今と同じ `caller_live_agent_unavailable`） |
| `result-submit`（`resolve_caller_live_agent_for_worker`）                       | `agent get`               | 代用した cwd で、既存の照合を行う                                               | 拒否（今と同じ `worker_context_unverified`）                   |
| 呼び出し元の自己回復（#121、`_try_recover_unique_party`）                       | `agent get` / `pane list` | 代用した cwd で、既存の手順を行う                                               | 拒否（今と同じ）                                               |
| completion: 依頼元の再開（`completion_commands.py` の `verify_live_party_cwd`） | `agent get`               | 代用した cwd で、既存の照合を行う                                               | 拒否（今と同じ）                                               |

- 「今と同じ」は、現行実装の挙動と同じであることを意味する。
- `pane_cwd` のときに新しく拒否するのは、Pattern A の依頼先選定だけである。新しいエラーコード・理由コードは作らず、既存の `worker_unavailable` に `details.reason` を足して表す。
- エラーコード・`details.reason`・警告のキーには、エージェント名を含めない。

---

<a id="sec-5"></a>

## 5. 可観測性と人間向けの報告

<a id="sec-5-1"></a>

### 5.1 #193 との境界

- **#193（前段。挙動を変えない）**: `role set` の stderr 警告と出力 JSON の `cwd_source`、`role show` の `cwd_source`、暫定運用の docs、`public-cli.md` への `cwd_source` の追記。本設計は、これらをそのまま前提とし、書き直さない。
- **本設計（#194）**: 検証の層での代用（[§3.1](#sec-3-1)・[§4](#sec-4)）、検証の層の警告と出力（[§5.2](#sec-5-2)・[§5.3](#sec-5-3)）、Pattern A の拒否、環境変数（[§6](#sec-6)）、依頼元のチャット報告（[§5.4](#sec-5-4)）、#193 の暫定運用の記述の置き換え。

<a id="sec-5-2"></a>

### 5.2 stderr 警告

`cwd_source = "pane_cwd"` の観測を検証に使った CLI 実行では、次の 1 行を stderr に出す。

```text
[herdr-orchestrator] warning: foreground_cwd unavailable for pane <pane_id>; verified with pane cwd <path> (cwd_source=pane_cwd).
```

- 1 プロセスの中では、同じペインにつき 1 回だけ出す。sync の結果待ちのように同じペインを何度も観測する場合も、1 回にする（CLI を実行するたびには毎回出る）。
- `role set` の警告は #193 のものを使い、本設計では足さない。
- 標準出力の JSON の形は変えない（[§5.3](#sec-5-3) の項目追加だけ）。

<a id="sec-5-3"></a>

### 5.3 出力 JSON

| コマンド                      | 追加項目                                                                                                                                                                                                                                                               |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handoff`                     | `cwd_sources: {"requester": "foreground" \| "pane_cwd", "worker": "foreground" \| "pane_cwd"}`。`pane_cwd` が 1 つでもあれば、既存の `warnings` 配列に `{"code": "foreground_cwd_unavailable", "party": "requester" \| "worker", "pane_id": ..., "cwd": ...}` を足す。 |
| `task-status` / `task-result` | `recipient_verification.actual_caller.cwd_source`                                                                                                                                                                                                                      |
| `role set` / `role show`      | `cwd_source`（#193 で入るもの。本設計では変えない）                                                                                                                                                                                                                    |

<a id="sec-5-4"></a>

### 5.4 人間向けチャット報告（references に記載）

- **依頼元**（`handoff` を実行したエージェント）: `handoff` の出力の `warnings` に `foreground_cwd_unavailable` がある場合、**同じ会話の中で初回だけ**、人間に次を報告する。
  - 「`<display_summary>` は `foreground_cwd` が取れないため、pane の `cwd`（`<cwd>`）で代用して配送した」
  - 2 回目以降は報告しない（毎回はうるさいため）。
  - 記載先: `skills/herdr-orchestrator/references/handoff.md`（委託後の手順）。
- **依頼先**: stderr の警告だけとし、チャットでは報告しない。この警告だけを理由に作業を止めず、`recipient_verification.verified` の判定に従う。記載先: `skills/herdr-orchestrator/references/worker-receive.md`（1 行）。
- #193 で `roles.md` / `handoff.md` に入る暫定運用（「`foreground_cwd` が無いペインには配送できない」）は、本設計の挙動（代用して配送する。警告が出る。厳格モードで従来どおりにできる）に書き換える。
- いずれも、エージェント名を条件に書かない。主語は「`foreground_cwd` を返さないペイン」とする。

---

<a id="sec-6"></a>

## 6. 一括して厳格に戻す環境変数

| 項目         | 仕様                                                                                                                                                                |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 名前         | `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD`                                                                                                                         |
| 値           | `1` = 厳格。未設定・空文字・`0` = 既定（代用あり）。                                                                                                                |
| 不正な値     | それ以外の値は、stderr に警告を出し、**厳格として扱う**（安全側）。                                                                                                 |
| 効く範囲     | [§3.2](#sec-3-2) の 2 か所だけ。結果として、検証の層のすべての経路が現行と同じ挙動になり、`role set` は `pane_cwd` の登録を拒否する。                               |
| 読み取り     | `dispatch_commands.py` の `stale_*_threshold_sec()` と同じく、関数 1 つ（例: `require_foreground_cwd() -> bool`）で読む。呼び出しのたびに読む（キャッシュしない）。 |
| 既存との違い | `HERDR_ORCHESTRATOR_STALE_*` は不正な値を既定値に戻すが、本変数は安全上の切り替えなので、不正な値を **厳格**として扱う。この違いは public-cli.md に明記する。       |

---

<a id="sec-7"></a>

## 7. 許容するリスクと段階的な引き締め

<a id="sec-7-1"></a>

### 7.1 リスクの整理

| 場合                                                                                                                                                                                   | 検知                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 代用した `cwd` 自体が、期待と **別のリポジトリ**を指している                                                                                                                           | §11.0 の git root / repository identity の照合、または `worker.cwd` との一致で止まる（既存の仕組み）。                                                                                                                                                                                                               |
| 登録後に状態が変わる（エージェントの入れ替え、別の場所での起動し直し等）で、pane の `cwd` もそれに追随する                                                                             | Role Binding 経路の依頼先選定では、既存の指紋照合で止まる。その他の経路は、`cwd` の変化を既存の照合が捉える範囲で止まる。                                                                                                                                                                                            |
| 代用した `cwd` が **古いまま期待どおりのリポジトリを指し**、実際の作業場所は別のリポジトリ（pane の `cwd` が起動し直しや `cd` に追随しない場合。依頼元・依頼先のどちらでも起こりうる） | **機械では検知しない**。既存の照合は、古い値どうしを比べるため通ってしまう。警告と `cwd_source = pane_cwd` の出力から人間が気づく必要があるが、表示されるパスは古い値なので、パスだけを見ると正しく見える点に注意する。`foreground` の場合は前景プロセスの cwd が追随するので起こらない（`pane_cwd` に固有の残余）。 |
| 代用した `cwd` が、実際の作業場所と **同じリポジトリの別の場所**を指している                                                                                                           | **機械では検知しない**。警告・出力・チャット報告から、人間が目視で気づく。                                                                                                                                                                                                                                           |

**許容する範囲**: 機械で止められずに残るのは、「代用した `cwd` が期待どおりのリポジトリを指している場合のずれ」である。これには、同じリポジトリ内でのずれと、古い `cwd` が残って実際は別のリポジトリで動いている場合（表の 3 行目）が含まれる。既存の照合で止まるのは、代用した `cwd` 自体が別のリポジトリを指す場合に限られる。表の 3 行目がどれだけ起こるかは、[§9.3](#sec-9-3) の項目 2 の実測で判断する。プロジェクトの判断として、厳格なチェックを増やさず、この残余は人間の目視で補う（Issue #194「許容するリスク」）。

<a id="sec-7-2"></a>

### 7.2 段階的な引き締め

1. **段階 0（既定）**: 本設計のとおり（代用あり、既存の照合、警告と出力）。
2. **段階 1（運用で即時）**: 誤配送が見つかった環境では、`HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=1` で現行の厳格な挙動に戻す。コードの変更は要らない。
3. **段階 2（設計変更）**: 誤配送が続く場合や、実測（[§9.3](#sec-9-3)）で `cwd` が古いまま残ることが分かった場合は、別 Issue で、既定の反転（未設定 = 厳格）や、追加の照合（例: `role set` 時の確認）を検討する。

観測した事例は [§18 実機記録](herdr-orchestrator-verification-log.md) に、観測例として残す（エージェント名は観測例としてだけ書いてよい）。

---

<a id="sec-8"></a>

## 8. 既存設計への影響

<a id="sec-8-1"></a>

### 8.1 Task Registry / Role Binding の保存形式

- Task Registry の schema（[§6](herdr-orchestrator/03-task-registry.md#sec-6)）: **変更なし**。`worker.cwd` には、今までどおり選定時の観測 cwd（`cwd_source` によらない）を保存する。
- Role Binding の保存形式（cross-workspace [§4.2](herdr-orchestrator-cross-workspace.md#sec-4-2)）: **変更なし**。

<a id="sec-8-2"></a>

### 8.2 改訂する文書

| 文書                                                                                         | 改訂内容                                                                                                                                                                                                    | 時期           |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| [06-results-and-workflows.md §11.0](herdr-orchestrator/06-results-and-workflows.md#sec-11-0) | 手順 1 を「[§3.1](#sec-3-1) の関数で検証用の cwd と `cwd_source` を決める」に改訂。「`foreground_cwd` を worktree root の代用にしない」は、「検証用の cwd を worktree root の代用にしない」として維持する。 | 本設計 PR      |
| [06-results-and-workflows.md §11.1](herdr-orchestrator/06-results-and-workflows.md#sec-11-1) | 「Herdr が `foreground_cwd` を返さない場合 … 拒否」を、代用と厳格モードの説明（本設計書へのリンク）に置き換える。                                                                                           | 本設計 PR      |
| [06-results-and-workflows.md §11.4](herdr-orchestrator/06-results-and-workflows.md#sec-11-4) | 送信直前の再確認・受信側の検証が [§4](#sec-4) に従うことを 1 行追加する。                                                                                                                                   | 本設計 PR      |
| [cross-workspace §4.2](herdr-orchestrator-cross-workspace.md#sec-4-2)                        | `cwd` の行に、`cwd_source` と本設計書へのリンクを追記する。                                                                                                                                                 | 本設計 PR      |
| [cross-workspace §7.4](herdr-orchestrator-cross-workspace.md#sec-7-4)                        | `actual_caller.cwd_source` を追記する。                                                                                                                                                                     | 本設計 PR      |
| `skills/herdr-orchestrator/references/public-cli.md`                                         | 出力項目（[§5.3](#sec-5-3) のうち本設計の分）、警告、環境変数、`role set` の厳格モード時のエラーコード、Pattern A の `details.reason`。                                                                     | 実装 PR（D-2） |
| `references/handoff.md` / `worker-receive.md` / `roles.md`                                   | [§5.4](#sec-5-4) の手順と、#193 の暫定運用の記述の書き換え。                                                                                                                                                | 実装 PR        |

---

<a id="sec-9"></a>

## 9. 実装・検証計画

<a id="sec-9-1"></a>

### 9.1 実装順

- 実装は **#193 のマージ後**に、`main` から行う。#193 で入る `cwd_source` の項目名・値は、本設計と同じもの（`"foreground"` / `"pane_cwd"`）を前提にする。食い違う場合は、本設計書を改定してから実装する。

<a id="sec-9-2"></a>

### 9.2 段階的な実装手順

1. `resolve_observed_cwd()` と `LiveAgentSnapshot.cwd_source` を追加し、`agent get` / `pane list` の両経路をこの関数に寄せる。挙動は変えない（検証の層は引き続き `pane_cwd` を拒否する）。
2. `require_foreground_cwd()` と、[§3.2](#sec-3-2) の 2 か所の適用点を追加する。
3. 検証の層の入口での一律拒否を外し、既定では代用した cwd で検証する。Pattern A の依頼先選定だけは拒否する（[§4](#sec-4)）。
4. 警告と出力 JSON（[§5.2](#sec-5-2)・[§5.3](#sec-5-3)）。
5. references と public-cli.md（[§5.4](#sec-5-4)、[§8.2](#sec-8-2)）。

<a id="sec-9-3"></a>

### 9.3 実機での確認項目（実装の前後に実施）

`foreground_cwd` が欠落するペインについて、次を観測項目として記録する。エージェント名は観測例としてだけ書く。項目 2 は、[§7.1](#sec-7-1) の表の 3 行目のリスクの大きさを決める観測なので、必ず実施する（`foreground_cwd` が欠落する環境が無い場合は、実施できなかったことを記録する）。

1. `herdr agent get <pane_id>` に `cwd` があるか。`cwd` は、エージェントを起動したディレクトリと一致するか。
2. エージェントを終了し、同じペインで別のディレクトリへ `cd` してから起動し直したとき、`cwd` が新しいディレクトリに変わるか（[§7.2](#sec-7-2) の段階 2 を判断する材料）。

<a id="sec-9-4"></a>

### 9.4 テスト計画（FC-01〜FC-09）

fake herdr の応答から `foreground_cwd` を省き、**CLI の実際の終了コード・標準出力 JSON・標準エラー・Registry / Role Binding ファイルの内容**で確認する（指針 10。引数の変換だけの検証にしない）。

| ID        | 観点                               | 前提・操作                                                                                                                         | 期待値                                                                                                                                                                                                                                                                                                                                                                                          |
| --------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **FC-01** | 取得元の決定                       | `resolve_observed_cwd` に (a) `foreground_cwd` あり、(b) `foreground_cwd` なし・`cwd` あり、(c) どちらもなし、(d) `cwd` が相対パス | (a) `"foreground"`、(b) `"pane_cwd"` と `cwd` の値、(c)(d) `LiveAgentError`。`agent get` 経路と `pane list` 経路の結果が一致すること                                                                                                                                                                                                                                                            |
| **FC-02** | Role Binding 経路の全工程（async） | 依頼先ペインが `pane_cwd`、`role set` で登録済み。依頼元が `handoff --mode async`、依頼先が `task-status` → `result-submit`        | `handoff`: `prompt_sent = true`、`cwd_sources.worker = "pane_cwd"`、`warnings` に `foreground_cwd_unavailable`、stderr に警告。`task-status`: `verified = true`、`actual_caller.cwd_source = "pane_cwd"`、stderr に警告。`result-submit` 成功。Registry の `result.status = succeeded`                                                                                                          |
| **FC-03** | Role Binding 経路の全工程（sync）  | FC-02 と同じ構成で `--mode sync`                                                                                                   | 結果待ちが完了し、`result_wait.completed = true`。`handoff` プロセスの stderr の警告は、依頼先ペインについて 1 回だけ                                                                                                                                                                                                                                                                           |
| **FC-04** | Pattern A は拒否                   | Role Binding なしの標準ロールで、依頼先ペインが `pane_cwd`                                                                         | `worker_unavailable`、`details.reason = "foreground_cwd_required"`。Registry に task が作られていない。prompt が送られていない                                                                                                                                                                                                                                                                  |
| **FC-05** | 厳格モードで全経路が拒否           | `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=1` で、`role set` と FC-02 と同じ操作                                                   | `role set` は `role_target_foreground_cwd_required` で、Role Binding ファイルが変わらない。`handoff` は `handoff_not_continuable` で、prompt は送られない（task は `created` のまま残る。現行と同じ）。既定モードで配送済みの task に、厳格モードで `task-status` を実行すると `verified = false`（`reason = "caller_live_agent_unavailable"`）、`result-submit` は `worker_context_unverified` |
| **FC-06** | 不正な環境変数値                   | `HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD=yes`                                                                                    | stderr に警告が出て、FC-05 と同じく厳格になる                                                                                                                                                                                                                                                                                                                                                   |
| **FC-07** | 別リポジトリの `cwd`               | リポジトリ A で登録した後、依頼先ペインの `cwd` がリポジトリ B を返す                                                              | `handoff` は `worker_context_changed` で、prompt は送られない。B 側で実行した `result-submit` は `worker_context_unverified`                                                                                                                                                                                                                                                                    |
| **FC-08** | completion（依頼元が `pane_cwd`）  | 依頼元ペインが `pane_cwd`。async handoff の後、依頼先が `result-submit`（依頼元は idle）                                           | `handoff` の `cwd_sources.requester = "pane_cwd"`。`completion.status = sent`。依頼元への prompt が送られている                                                                                                                                                                                                                                                                                 |
| **FC-09** | 回帰（`foreground_cwd` あり）      | 全ペインで `foreground_cwd` を返す fake herdr で、FC-02 と同じ操作                                                                 | stderr に `foreground_cwd unavailable` の警告が出ない。`cwd_sources` はすべて `"foreground"`。既存のテストがすべて通る                                                                                                                                                                                                                                                                          |

- 合わせて、`npm run test:orchestrator`、`npm run verify:orchestrator`、`npm run format:md && npm run check:md` を通す。
- 実装の差分に、エージェント名を条件にした分岐や文言が入っていないことを、レビューで確認する（機械検査は作らない。指針 8）。
