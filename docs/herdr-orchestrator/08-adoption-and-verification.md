# 導入・動作確認・Repository 移行

> [設計書全体の目次](../herdr-orchestrator-design.md)

<a id="sec-17"></a>

## 17. 導入

<a id="sec-17-1"></a>

### 17.1 Herdr の確認

```bash
herdr --version
herdr --help
```

既存環境では再インストールせず、現在利用している Herdr を確認する。

<a id="sec-17-2"></a>

### 17.2 Herdr 公式 Skill

Herdr 公式が提供する Skill を利用する。

利用中の Herdr が対応 Skill
の取得機能を提供する場合、そのリリースに対応する Skill を優先する。

例:

```bash
herdr --skill
```

公式 Skill 配布方式を利用する場合の例:

```bash
npx skills add herdrdev/herdr --skill herdr -g
```

実際の導入時には、その時点の Herdr 公式ドキュメントおよび CLI help
を確認する。

<a id="sec-17-3"></a>

### 17.3 中央 Orchestrator Skill

中央リポジトリから `herdr-orchestrator` をユーザーグローバル Skill
領域へ配置する。

個別 repository には配置しない。

```text
User
├── Herdr
├── Official herdr Skill
└── herdr-orchestrator

Repositories
├── project-a
├── project-b
└── project-c
```

Cursor / Codex / Antigravity のユーザーグローバル Skill
配置先は、導入時点の各製品公式仕様を確認する。

Task Registry cleanup は `herdr-orchestrator` 内蔵の lazy cleanup を使用し、利用者に cron / systemd timer 等の追加設定を要求しない。

各 Agent の allowlist / approval 設定は任意の最適化とする。設定しなくても動作できることを要件とし、設定する場合は [§15](07-skill-and-implementation.md#sec-15) Approval Minimization の非破壊的 command surface のみを候補とする。

---

<a id="sec-18"></a>

## 18. 動作確認

<a id="sec-18-1"></a>

### 18.1 基本確認

以下を確認する。

- Herdr が起動している。
- 公式 Herdr Skill が利用可能。
- `herdr-orchestrator` が利用可能。
- reviewer Agent を識別できる。
- 各動作確認シナリオで **必要な role だけ** live Agent を起動し、その role の期待 `agent.name` が [§4.1](02-roles-and-task-contract.md#sec-4-1) どおり解決できる（4 role すべての常駐は [§4](02-roles-and-task-contract.md#sec-4) で要求しない）。
- handoff 入力の worker role が [§4](02-roles-and-task-contract.md#sec-4) の委譲先表と一致する（テスト→`implementer`、設計調査→`design` 等）。
- Registry の `requester.role` が requester の `agent.name` から導出され、[§8.5](04-handoff-and-requester.md#sec-8-5) と矛盾しない。
- [§4.1](02-roles-and-task-contract.md#sec-4-1) の命名: `len(<project-slug>-<role>) ≤ 32` では通常形式、**33 文字以上**で短縮形式 `<prefix>-<hash4>-<role>` に切り替わること（32 文字ちょうどは通常形式のまま）。
- 長い identity でも worker / requester の期待 `agent.name` が CLI・Registry・[§8.5](04-handoff-and-requester.md#sec-8-5) で同一であること。
- 数字始まりの identity（正規化後 `r-` 付加）で Herdr regex を満たす `agent.name` が生成されること。
- 期待 `agent.name` の live Agent が存在するが repository / worktree context が不一致のとき、handoff が **中止**され別名へ切り替わらないこと。
- requester を `main` へ rename せず利用できる。
- 日常的な orchestration が固定 `herdr-orchestrator` CLI から実行できる。
- 複雑な Herdr CLI 列を Agent が毎回生成しなくてもよい。

<a id="sec-18-2"></a>

### 18.2 Sync Review

以下を実行する。

```text
レビュー依頼を出して
```

確認項目:

1. Repository 固有 Skill が review request を生成する。
2. `herdr-orchestrator` が `reviewer` を選択する。
3. sync handoff が実行される。
4. reviewer がレビューを実施する。
5. reviewer の処理完了後 requester が復帰する。
6. requester が review response を開始できる。

<a id="sec-18-3"></a>

### 18.3 Async Handoff

sync 動作確認後、async を検証する。sync handoff についても同じ canonical schema / Registry が作成され、`completion.status = not_applicable` になることを別途確認する。

確認項目:

1. `schema_version = 1` の canonical nested schema だけが Registry の正規形式として使用される。
2. Registry JSON に旧 flat key `task_id` / `task_type` / `task_status` / `result_ref` が存在しない。
3. `task.status` / `dispatch.status` / `result.status` / `completion.status` の4軸が独立して遷移する。
4. Registry JSON の writer が `task-registry.py` に限定され、Agent が直接 JSON を更新しない。
5. `handoff` が `HERDR_ENV=1` 未設定時に Herdr CLI/API を呼ばず失敗する。
6. dispatch timeout/stalled は `dispatch.status=uncertain` / `task.status=unknown` となり、自動再送されない。
7. sync handoff も Registry に保存され、`completion.status=not_applicable` となる。

8. `task.id` が UUIDv4 形式で Orchestrator により生成される。
9. 不正な `task.id` および path traversal を拒否する。
10. Task Registry に `<task.id>.json` が作成される。
11. Registry root / task file / lock file の permission が設計値を満たす。
12. Registry root / task / lock の symlink を拒否する。
13. requester / worker / repository / worktree が記録される。
14. worker 候補選定時に cwd を補助情報として利用できる。
15. worker 自身が [§11.4](06-results-and-workflows.md#sec-11-4) に従い `task-status` で Registry を読み、repository / worktree / 自身の agent.name を再検証する（prompt だけでは作業開始しない）。
    15a. handoff 手順 7 直後（`mark-dispatch` 前）に worker が `dispatch.status=pending` を読んでも、§11.4 4a の bounded 再 poll 後に `sent` / `uncertain` となれば context 再検証のうえ task を開始できる。
    15b. 受信側 poll 中に `dispatch.status` が `failed` / `skipped` に terminal 化した場合、worker は task 作業を開始しない。
    15c. §11.4 4a の retry 上限到達後も `pending` の場合、worker は task 作業を開始せず `dispatch_pending_timeout` を報告する（後から `sent` になれば 15a と同様に再開可能）。
    15d. 15c のあと `mark-dispatch` または reconcile で `dispatch.status=sent` になった task について、人間または repository workflow が `task-status` を再確認し、§11.4 手順 3–5 を満たせば **新 prompt なし**で worker が task を開始できる。
16. repository / worktree 不一致時に task を開始しない。
17. branch / commit を指定した workflow では必要な一致検証を行う。
18. worker へ handoff される。
19. durable result が保存される。
20. `result.ref` が Task Registry に記録される。
21. `result.ref` の絶対 path、`../`、`context.worktree.path` 外 symlink を拒否する。
    21a. `result.status ∈ {succeeded, failed}` で `result.ref=null` または artifact 不存在の submit を拒否する（[§10](06-results-and-workflows.md#sec-10)）。
    21b. `result.status=unavailable` で `result.ref≠null` の submit を拒否する。
22. `task.id` だけから Task Registry を検索し、安全に成果物へ到達できる。
23. `completion.action = resume_requester` が動作する。
24. requester が `idle` / `done` の場合だけ再開される。
25. requester の live `agent.name` が解決できない場合、pane ID fallback を行わず `skipped/requester_unavailable` になる。
26. requester が `working` / `blocked` / `unknown` の場合は自動再開されず `skipped` になる。
27. `completion.action = none` では Agent への自動入力を行わない。
28. completion の `pending -> processing -> sent|failed|uncertain|skipped` が記録される。
29. 同じ task に対して複数実行主体が completion を処理しても、1つだけが `processing` を claim できる。
30. `processing` / `sent` の task に対する重複 completion が自動実行されない。
31. completion 失敗後も `task.id -> result.ref` から結果を回収できる。
32. `processing` stuck task を inspect / reset で明示復旧できる。
33. lazy cleanup が task terminal かつ completion terminal の task のみ削除し、`completion.status=pending|processing` や未完了 task を保持する。
34. Herdr Space / Agent sidebar で Agent 状態を確認できる。
35. `task-status <task.id>` で task 単位の状態を確認できる。
36. `task-result <task.id>` で result.ref / durable result を確認できる。
37. 正常 completion では正常完了時に中央 Orchestrator が人向け push 通知を必須としないことを確認する。
38. requester チャットから開始した初回 handoff で target 不在・context mismatch 等により配送不能となった場合、fallback せず requester が同じチャットへ即時報告する。
39. timeout / stalled 等で配送結果が不明な場合、blind resend せず「配送結果不明」として requester チャットへ報告する。
40. 配送不能通知から人間が Herdr Space の対象 Agent 状態を確認し、運用対処できることを確認する。
41. live `agent.name` 解決後、repository / worktree / agent kind 等の context sanity check を行う。
42. context を安全に確認できない場合、別 target へ fallback せず `skipped` になる。
43. 配送不能時、requester Agent が同じチャットで人間へ報告する。
44. timeout / stalled 等の配送結果不明時、`uncertain` として記録し、自動再送せず人間へ報告する。
45. `agent_session_id` / `terminal_id` / 古い pane ID を callback fallback に使用しない。
46. 依頼元 Agent 個体が終了後、同じ `agent.name`・repository identity・worktree path を満たす別 live Agent が requester role の継承先として callback を受けられる。
47. 同じ `agent.name` でも repository identity または worktree path が異なる場合は `skipped / requester_context_mismatch` となる。
48. workflow が branch / commit 固定を要求する場合、その不一致で callback が `skipped` となる。
49. workflow が branch / commit 固定を要求しない場合、未 commit 差分を扱う review で commit 一致を必須にしない。
50. 同一 Agent 個体を必須とする workflow では自動 `resume_requester` を行わず、durable result を保持したまま `skipped` となる。
51. Task Registry に `task.instruction` が保存され、元の会話履歴なしでも依頼内容を復元できる。
52. Result Contract の `summary` が Task Registry に保存される。
53. `resume_requester` の completion prompt は `task.id` と Registry 読取指示のみを含み、requester が `task-status` / `task-result` から `task.type` / `task.status` / `result.status` / `result.ref` / `result.summary` を取得できる。
54. 別 Agent が requester role を継承した場合でも、completion prompt の `task.id` から Registry を読み直して後続処理を再開できる。
55. completion prompt の情報だけを正本とせず、Task Registry / repository成果物を正として再確認する。
56. requester callback の timeout / stalled は `completion.status=uncertain` となり、blind resend されない。
57. `result.status=unavailable` の Registry result では `result.ref: null` を許可し、requester が `result.summary` から失敗理由を確認できる。
58. `context.repository.identity` は論理 repository identity、`context.worktree.path` は task が操作する checkout/worktree root として区別される。
59. `result.ref` は `context.worktree.path` 基準で解決され、別 worktree や repository 共通領域へ escape できない。
60. async の初回 dispatch が definite failure / safety skip の場合、`task.status=failed` / `result.status=unavailable` とする。`completion.action=resume_requester` なら `completion.status=skipped`、`completion.action=none` なら `not_applicable` を維持し、いずれも completion `pending` を残さない。
61. `dispatch.status=uncertain` / `task.status=unknown` の後に worker success result が届くと、`dispatch.status=uncertain` を保持したまま `result.status=succeeded` / `task.status=succeeded` へ解消される。
62. `dispatch.status=uncertain` / `task.status=unknown` の後に worker failed/unavailable result が届くと、`dispatch.status=uncertain` を保持したまま `task.status=failed` へ解消される。
63. `result.status=failed` および `result.status=unavailable` の submit が `task.status=failed` を同一 atomic update で設定する。
64. `completion.status=uncertain` は自動再送されず、inspect + explicit reset の場合だけ `pending` に戻せる。
65. state machine に定義されていない逆行・不正遷移を `task-registry.py` が拒否し、既存 state を変更しない。
66. sync `handoff` が `--wait` なしで prompt 送信後・[§6.14](03-task-registry.md#sec-6-14) の result 待機前に `mark-dispatch` し、`dispatch.status=pending` の task へ `result-submit` できない。
67. `result-submit` が Registry の `worker.agent_name` と一致しない submit 元を拒否する。
    67a. 同名 live Agent でも `context.repository.identity` または `context.worktree.path` が Registry と不一致なら拒否する。
    67b. submit 元の repository / worktree を安全に確認できない場合、`worker_context_unverified` として拒否する。
    67c. worker の `foreground_cwd` がリポジトリサブディレクトリでも、`rev-parse --show-toplevel` 経由で Registry の `context.worktree.path` と一致すれば `result-submit` が受理される。
    67d. Git 管理外・`rev-parse` 失敗・repository identity 一意決定不能の submit は拒否される。
68. terminal result への同一 payload 再 submit が idempotent replay として成功し、異なる payload は拒否される。
69. async `resume_requester` で worker の `result-submit` 成功後、同一 CLI 内で completion が best-effort 起動される。
70. durable result 確定後の completion 連鎖失敗について、`resume-task` または `reset-completion` + `resume-task` で明示再試行できる（初回 `dispatch=failed|skipped`・worker 未受領 terminal では不可。108–109）。
71. handoff 手動再試行は新 `task.id` を発行し、旧 task を上書きしない。
72. MVP では public task cancellation CLI がなく、`cancelled` へ遷移しない。
73. `handoff` が Registry 作成前に live worker を解決し、`create` 時点で `worker.agent_name` / `worker.pane_id` が必須で記録される。
74. `record-prompt-start` 後に `mark-dispatch` 前で停止した task を `doctor` が stale 候補として報告する。
75. `reconcile-dispatch` が blind resend せず dispatch terminal を確定でき、`result-submit` が受理される。
76. `dispatch.status=pending` かつ `herdr_prompt_started_at` 設定済みの task へ、reconcile 前の `result-submit` が拒否される。
77. worker が `working` / `blocked` / `unknown` のとき、同一 `<project-slug>-<role>` の別 live Agent へ自動切替せず、同一 worker の idle 化を待つかユーザーへ報告する（別 `agent.name` の自動起動は行わない）。
78. `dispatch.status=pending` への `result-submit` が retryable 拒否となり、Registry が更新されない。
79. worker workflow が `dispatch_not_ready` を受けた後、同一 payload で bounded retry し、dispatch terminal 後に result が登録される。
80. `reconcile-dispatch` 後、`dispatch.status ∈ {sent, uncertain}`・`result.status=pending`・`task.status ∈ {in_progress, unknown}`（[§10.2](06-results-and-workflows.md#sec-10-2)）の task へ同一 payload の `result-submit` が成功し、必要なら completion が連鎖する。
81. live worker 未解決で `create` 前に失敗した handoff は Registry JSON を作成せず、`task.id` を発行しない。
82. `create` 前失敗時、人間起点 handoff では requester が配送不能をチャット報告する。
83. `create` 後・`herdr_prompt_started_at=null` の stale task を `doctor` が報告する。
84. `reconcile-dispatch` が prompt 未開始（A）を確認し、blind resend せず `failed` / completion `skipped|not_applicable` へ確定できる。
85. `reconcile-dispatch` が worker 側に task 痕跡がある場合、A から B 手順へ切り替えられる。
86. `record-prompt-start` が `task.status=created` かつ `dispatch.status=pending` 以外を拒否する。
87. `reconcile-dispatch` が A/B precondition 不一致時に Registry を変更せず conflict を返す。
88. `reconcile-dispatch` で `failed` 確定後、同 task へ再開した handoff が `record-prompt-start` を拒否し、`agent prompt` を送信しない。
89. handoff が `record-prompt-start` 成功後、`reconcile-dispatch` (B) が dispatch terminal 化した task へ `mark-dispatch` が二重確定しない。
90. handoff の `record-prompt-start` と `reconcile-dispatch` (A) が競合した場合、lock 下 precondition の一方のみが成功する。
91. sync handoff が `result.status` terminal を正常終了条件とし、prompt 直後の `idle` のみでは成功扱いしない。
92. sync で result 未確定期間、requester が worker へ prompt / result 再送を促さない（Registry poll または task-status 確認）。
93. 生存プロセスが handoff flock 保持中、`reconcile-dispatch` が dispatch terminal 化しない（TTL 奪取なし）。
94. reconcile が task を failed 確定後、再開 handoff が `assert-handoff-continuable` で止まり `agent prompt` しない。
95. public `reset-completion` が recovery 履歴付きで `pending` に戻せる（初回配送失敗 terminal パターンでは拒否。108）。
96. public `recover-completion` が §9.14 を満たす場合に reset し、続けて §9.13 を満たす場合のみ claim する（reset のみで終了可）。
97. `resume-task` / `recover-completion` が `result`・`dispatch` 未確定の task で claim しない。
98. A 手順で worker 痕跡あり・timestamp null の task を B' 手順で修復できる。
99. `create` 前配送不能通知に `task.id` が無くても、role / repository 等の必須項目が含まれる。
100. `context.worktree.branch|commit` が非 null の task で、不一致時に completion が skipped になる。
101. handoff が flock 下で assert 成功後、生存中に reconcile が terminal 化しない。
102. sync result 待機 timeout 時、Registry 4 軸を勝手に変更せず、`dispatch=uncertain` なら `task.status=unknown` を維持する。
103. worker 不在かつ `dispatch=sent` で sync 異常終了後、`reconcile-dispatch` は拒否され、`result-submit` または §10.2 で result 回収できる（prompt 再送なし）。
104. `reset-completion` が §9.14 のみで受理し、§9.13 を要求しない。
105. `reconcile-dispatch` が `dispatch.status=pending` 以外の task を拒否する。
106. handoff wrapper が flock fd を保持中、別プロセスの `reconcile-dispatch` が flock 取得に失敗する。
107. `reconcile-dispatch`（引数 `task.id` のみ）が §6.12 A 手順 4b 既定どおり Registry を変更せず終了する。`--dispatch-outcome uncertain` で 4a、`--dispatch-outcome failed` で手順 5（未配送確定）の terminal 化ができる。
108. 初回 `dispatch=failed|skipped`・`task=failed`・`result=unavailable`・`completion=skipped` の task で `reset-completion` が拒否され、`resume-task` が claim しない（[§9.13](05-completion-and-recovery.md#sec-9-13)・[§9.14](05-completion-and-recovery.md#sec-9-14)）。
109. 上記 terminal パターン以外で `completion=skipped`（例: requester `working`）の task は `reset-completion` 後に §9.13 を満たせば `resume-task` できる。
110. `assert-handoff-continuable` 直前に worker state が `working` に変化した場合、handoff が prompt せず fail closed する（または dispatch を uncertain/failed 相当で止める）。
111. 同一 `agent.name` で context 不一致の live Agent が worker 役を受けた場合、受信側 [§11.4](06-results-and-workflows.md#sec-11-4) 検証で作業を開始しない。
112. completion prompt 後、requester が `task-result` 確認前に後続 workflow を開始しない（§11.4）。
113. `reconcile-dispatch --dispatch-outcome failed` が `--dispatch-reason` なしで拒否される。
114. repository identity 正規化が origin の fetch URL を全件（`get-url --all` 相当）評価する。
115. origin に複数 fetch URL があり正規化後の identity が一致しない場合、handoff / `result-submit` が拒否される。
116. HTTPS remote URL に userinfo が含まれても、Registry には論理 identity のみ保存され認証情報は残らない。
117. scp 形式 SSH URL と HTTPS URL が同一 repository を指す場合、同一 canonical identity（例: `github.com/org/repo`）になる。
118. `ssh://user@host/path`（port 省略または 22）形式の remote URL も、同一 repository なら (117) と同じ canonical identity になる。
119. `ssh://host:2222/org/repo` のように **22 以外の port** を含む URL は `host:2222/org/repo` となり、同一 host・path の `host/org/repo`（port 22・HTTPS・scp）と **一致しない**。
120. HTTPS URL の port が省略または **443** の場合、canonical identity は `host/org/repo`（`:443` を含まない）。
121. `https://host:8443/org/repo` のように **443 以外の port** を含む URL は `host:8443/org/repo` となり、(120) の同一 host・path と **一致しない**。
122. `origin` と `upstream` が異なる repository identity に正規化される worktree では handoff / `result-submit` が拒否される。
123. path が同形でも host が異なる URL（例: `github.com/org/repo` と `gitlab.com/org/repo`）は **異なる** canonical identity になる。
124. remote URL の hostname が大文字を含む場合（例: `https://GitHub.com/org/repo`）、canonical identity の host は **小文字**（`github.com/org/repo`）になる。
125. remote URL の path 部分の大文字小文字は **保持**され、`Org/Repo` と `org/repo` は **異なる** canonical identity になる（同一 host でも path 大小文字のみの差は別 repository として扱う）。

<a id="sec-19"></a>

## 19. 既存 Repository の移行

<a id="sec-19-phase-1"></a>

### Phase 1: 中央 Skill 導入

既存 `herdr-review` は維持する。

中央 `herdr-orchestrator` を導入し、既存処理と並行して検証する。

<a id="sec-19-phase-2"></a>

### Phase 2: Sync Handoff

通常レビューを sync handoff へ移行する。

以下への依存を外す。

```text
ensure-reviewee-main.sh
main rename
reviewee_target
```

<a id="sec-19-phase-3"></a>

### Phase 3: Callback 整理

通常レビューから callback を削除する。

callback は async handoff 用へ一般化する。

<a id="sec-19-phase-4"></a>

### Phase 4: Herdr CLI 重複削除

repo Skill に存在する Herdr CLI 操作手順を削除する。

公式 Herdr Skill を利用する設計へ変更する。

<a id="sec-19-phase-5"></a>

### Phase 5: 回帰確認

導入対象の各 repository で確認する。

問題がなければ中央方式への移行を完了する。

---
