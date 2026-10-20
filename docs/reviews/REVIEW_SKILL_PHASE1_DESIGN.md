# 内部レビュースキル改善 Phase 1 設計書

> 状態: 第4ラウンドの技術設計レビューは GO。バックアップを外した設計書の第1ラウンド指摘を反映。再レビュー・実装承認待ち。
>
> 対象: このリポジトリの内部レビュー。比較対象の固有情報は記載しない。

本書は [Issue #61](https://github.com/okinawa-systembit/herdr-orchestration/issues/61) の計画と R61-01〜R61-11 の設計対応を、このリポジトリ固有の仕様としてまとめる。中央 Orchestrator の共通 Task Contract、状態遷移、Result Contract は既存の[設計書目次](../herdr-orchestrator-design.md)から参照し、本書では複製しない。

## 目次

- [1. 適用範囲と承認状態](#sec-1)
- [2. 正本と識別子](#sec-2)
- [3. 配送とレビュー実行](#sec-3)
- [4. 履歴の保存と採番](#sec-4)
- [5. 保持範囲と記録喪失時の扱い](#sec-5)
- [6. Result 登録と障害復旧](#sec-6)
- [7. 公開防止と権限](#sec-7)
- [8. 移行と改修対象](#sec-8)
- [9. 受け入れ検証](#sec-9)
- [10. 運用上の確定事項](#sec-10)

<a id="sec-1"></a>

## 1. 適用範囲と承認状態

Phase 1 はこのリポジトリの Herdr 内部レビューの品質基準、配送ファイル、非公開履歴を対象とする。比較対象のリポジトリ、中央 Orchestrator の共通スキーマ、GitHub PR と Finding Issue の管理方式は変更しない。

バックアップを必須とする技術設計は Issue #61 の第4ラウンド再レビューで GO と判定された。その後の運用判断で、非公開レビュー記録の正本を worktree 内に置き、バックアップを作らず、HDD 障害、PC リプレース時の移行忘れ、意図的な clean・worktree 削除による記録喪失を許容することが決まった。本改訂はその GO の対象外であり、実装・テストも未実施である。[§10](#sec-10) の確定事項を踏まえた設計差分を確認し、実装承認を受けるまで Skill、スクリプト、配送先を切り替えない。

レビュー品質には次を導入する。

| 項目                  | Phase 1 の方針                                                                                                                           |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Review Tier           | LIGHT、STANDARD、DEEP を差分規模と契約・権限・状態遷移・並行処理・セキュリティへの影響で選ぶ                                             |
| モデル選択            | 利用可能な機能が確認できる場合だけ Tier に合う能力を選ぶ。切替不能でもレビューを継続し、追加検討は未解決の論点に絞る                     |
| セキュリティ Baseline | 既存の[レビューガイドライン](../herdr-agent-review-guidelines.md)と固有設計を参照するチェックリストとし、規範の正本を増やさない          |
| 品質ゲート            | すべての Tier で2段階レビュー、既存保証の確認、指摘の3分類、反証確認、P0〜P3と Blocking の分離、未実施検証の明示、既存 GO 条件を維持する |
| 通知                  | sync handoff の結果待ちと既存 completion を利用する。reviewer 独自の callback を追加しない                                               |

<a id="sec-2"></a>

## 2. 正本と識別子

| 情報                               | 正本                                                              | ID・参照                                            |
| ---------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------- |
| Task、dispatch、result、completion | [Task Registry](../herdr-orchestrator/03-task-registry.md#sec-6)  | Registry が発行する Task ID                         |
| 配送前の依頼と一時応答             | Git 除外した `.inbox/review/`                                     | 一時パストークン、発行後は Task ID                  |
| 内部レビューの個別記録             | worktree 内で Git 除外した `docs/reviews/internal/<instance-id>/` | Review ID、初回 Review ID を再利用する Chain ID     |
| 内部レビューの一覧                 | `docs/reviews/internal/<instance-id>/INDEX.md`                    | 個別記録から再構築できる派生索引                    |
| 採番と登録の補助状態               | Git common directory 内の非公開状態領域                           | 共有 lock、high-water mark、worktree 登録、記録台帳 |
| Scope と GitHub Issue の対応       | 既存の [docs/reviews/INDEX.md](INDEX.md) と GitHub                | 既存の R001〜R014、I001〜I005                       |
| PR レビュー・Finding Issue         | GitHub                                                            | 既存の番号、権限、クローズ条件                      |

内部レビューには `RV001`、`RV002`、…、`RV1000` のように最小3桁で表示する Review ID を使用し、上限は設けない。Scope ID の `R001` と区別する。初回レビューの Chain ID はその Review ID と同じ値とし、Follow-up は新しい Task ID と Review ID を取得して元の Chain ID と直前の Review ID を記録する。共通 Task Registry に Review ID を追加しない。

個別記録にはローカルの repository instance ID、Task ID、Review ID、Chain ID、Follow-up 元、レビュー対象の commit または未コミット差分の識別、前回指摘ごとの対応状態、依頼内容のハッシュ、判定、実施・未実施の検証、固定した Result Payload を保存する。`result.status=succeeded` はレビュー作業の結果登録を表し、レビュー判定の GO を意味しない。

<a id="sec-3"></a>

## 3. 配送とレビュー実行

現行の `handoff` は Task ID を内部で発行してから worker に通知する。公開 CLI に Task ID の事前発行機能はないため、requester が `.inbox/review/<task-id>/request.md` を先に作る方式は採らない。[handoff の契約](../herdr-orchestrator/04-handoff-and-requester.md#sec-7)に従い、次の順序にする。

1. requester は [§5 の世代・worktree 確認](#sec-5)後に `.inbox/review/pending/<random-token>/request.md` を排他的に作成し、短い相対パスを `handoff --instruction` に載せる。一時パストークンは配送前の衝突回避用で、Task ID や Review ID の代わりに使用しない。`--instruction` はファイル内容を取り込む機能ではない。
2. worker は通知された Task ID で `task-status` を読み、Registry の instruction、worker、repository identity、worktree を照合する。一致しなければ依頼を読んだだけで作業を開始せず、既存の[worker 受信手順](../../skills/herdr-orchestrator/references/worker-receive.md)に従う。
3. worker は仮置き依頼を読み、必要なら `.inbox/review/<task-id>/request.md` に排他的に写す。同名ファイルがあればハッシュ一致時だけ再利用し、異なれば停止する。移動・複写に失敗しても仮置き原本を残す。保存前にも世代と Registry context を再確認し、旧世代の処理を新世代の記録として保存しない。
4. [repo-review Skill](../../.agents/skills/repo-review/SKILL.md) の2段階レビュー、指摘の反証確認、既存保証の確認を実施する。Tier は調査範囲を決めるが品質ゲートの省略には使わない。
5. [§4](#sec-4)〜[§6](#sec-6) に従って記録・result 登録を行う。requester は sync handoff の終了だけを信用せず、`task-result` と記録を確認する。

仮置き依頼は Registry の terminal result、個別記録と `result.ref` の有効性、復旧処理がないことを確認した後にのみ削除できる。Task ID が発行されなかった handoff 失敗や dispatch/result が不確定な依頼は自動削除しない。作成後30日の経過は手動確認の目印とし、依頼者または運用管理者が再依頼の有無を確認する。`uncertain` を未配送と断定して自動再送しない。

<a id="sec-4"></a>

## 4. 履歴の保存と採番

Phase 1 の採番名前空間はローカルの Git common directory を共有する repository instance ごととし、登録した単一 worktree からの保存のみ許可する。Git common directory 内の Git 管理対象外の非公開状態領域にローカル instance ID、repository identity、許可する worktree の実パス、採番用 lock、high-water mark、確定済み Review ID・Task ID・記録ハッシュの台帳を保持する。この領域は別 worktree からの同時採番を止め、記録欠落を検出するための補助状態であり、レビュー本文のバックアップではない。個別記録は `docs/reviews/internal/<instance-id>/RVNNN.md` に保存し、`result.ref` にも instance ID を含める。保存処理は共有 lock 下で登録情報を照合し、別 worktree からの要求を拒否する。

個別記録がレビュー履歴の正本であり、INDEX と補助状態は単独では履歴の正本にならない。保存処理は共有 lock 下で次の順序を守る。

1. 登録済み worktree の個別記録、確定済み記録の台帳、high-water mark を照合する。Registry result が残り、記録も存在する Task ID では payload も照合する。記録が欠落した ID は[§5 の旧 Task 確認](#sec-5)を行って台帳に欠落状態を残し、採番済みと扱う。旧 Task の処理状況と新規 Task の次番号は別に管理する。記録のハッシュ差異、ID・Task ID の重複、symlink、台帳の破損は欠落と区別して停止する。INDEX 欠落だけなら残存する個別記録から再生成する。
2. 整合時だけ `max(個別記録の番号、確定済み記録の台帳の番号、high-water mark) + 1` を次番号とする。high-water mark を原子的に更新し、ファイルと親ディレクトリを永続化してから個別記録を公開する。この後のクラッシュによる欠番は再利用しない。
3. 個別記録は一時ファイルで完成させ、永続化後に既存ファイルを上書きしない方法で公開する。初回保存時に本文と `result.status / result.ref / result.summary` を固定する。記録が残る同一 Task ID の再処理では既存の Review ID と payload をバイト単位で再利用し、本文を上書きしない。記録が失われた Task ID は再処理しない。
4. 完成した個別記録の ID、Task ID、ハッシュを台帳に原子的に反映して永続化し、INDEX を個別記録から更新する。個別記録公開後・台帳更新前の中断は、記録の Task ID と payload を検証して台帳を補完する。整合性判定が終わるまで共有 lock を保持する。

個別記録公開前に high-water mark だけ進んだ場合は欠番として保持する。台帳にある記録が失われた場合も、その Review ID と Task ID の対応を欠落状態として保持し、番号を再利用しない。欠落した記録を参照する旧 Task ID の replay や `result-submit` は行わず、Registry の terminal result も巻き戻さない。新規依頼には次の ID を発行できるが、旧 Task の未完了状態は解消したと見なさない。Git common directory の補助状態が消失した場合は旧番号空間を継続せず、[§5](#sec-5) に従って残存記録をアーカイブして新しい instance を開始する。

Follow-up では前回の Review ID、Chain ID、対象 commit または差分識別子、指摘ごとの対応済み・部分対応・未対応・誤検知・検証不能を記録する。親記録の内容は上書きしない。親記録が失われた場合は前回指摘の対応を検証不能と明記し、通常の Follow-up として GO にしない。必要な範囲を再調査した新規の初回レビューを新しい Chain ID で開始し、旧 Chain は検証不能な参考情報としてのみ示す。既存の[Finding Issue 管理](../../.agents/skills/review-finding/SKILL.md)は変更しない。

<a id="sec-5"></a>

## 5. 保持範囲と記録喪失時の扱い

内部レビューの本文と固定済み Result Payload は `docs/reviews/internal/<instance-id>/` の個別記録だけに保存する。バックアップや別媒体への複製は Phase 1 の要件から外す。補助状態の台帳には本文や payload を保存しない。Task Registry の[保持期間](../herdr-orchestrator/03-task-registry.md#sec-6-8)とは別に、Phase 1 ではレビュー記録の自動期限削除を実装しない。通常運用での保持期間・廃止手順は自動アーカイブとともに別フェーズで決める。

通常運用では、記録の経過日数やレビュー判定 GO を条件とした自動アーカイブを Phase 1 に導入しない。[§4](#sec-4) の Follow-up と再参照に使用するため、保存した個別記録のパスを維持する。後日、一覧の整理が必要になれば、個別記録を移動せず INDEX 上で完了 Chain を区分する論理アーカイブを検討する。以下の「補助状態喪失時のアーカイブ」は、この通常運用上の整理とは別の世代切替処理である。

worktree 内の Git 除外ファイルは `git clean -fdx` / `git clean -fdX`、worktree 削除、HDD 障害、PC リプレース時の移行忘れなどで失われ得る。これらによる記録喪失は運用上許容し、Phase 1 では削除前チェックや削除阻止の仕組みを実装しない。消えた本文を自動復元できるとは約束しない。削除後の新規依頼には、残った採番状態に応じて[本節の喪失時ルール](#sec-5)を適用する。

この喪失範囲はレビュー専用のローカルデータである。`.inbox/review/` の一時依頼・応答は worktree とともに失われる。Git common directory の補助状態は `git clean -fdx` / `git clean -fdX` では消えないが、通常 checkout 全体の削除や HDD 障害では失われる。中央 Task Registry は[独立したローカル状態領域](../../skills/herdr-orchestrator/scripts/orchestrator/paths.py)に置かれ、worktree 削除だけでは必ずしも消えない。ただし terminal task は[保持期間](../herdr-orchestrator/03-task-registry.md#sec-6-8)後に削除され得る。GitHub の PR・Issue と Git で追跡する公開文書は、このローカルレビュー記録の削除対象に含めない。

個別記録が失われ、Git common directory の台帳が残る場合は、欠落した ID と Task ID を台帳に記録し、high-water mark を維持したまま次の新規 Task ID に次番号を発行する。欠落した記録が保存途中だったか Registry 登録済みだったかは、次番号の決定条件にしない。残存記録に内容不一致がある場合は、喪失として処理せず停止する。欠落 ID の本文と固定済み Result Payload は復元できない。

欠落を検出した時点で、台帳に残る各旧 Task ID を `task-status` で確認し、欠落状態とともに確認結果を記録・報告する。`result.status` が terminal (`succeeded` / `failed` / `unavailable`) なら Registry の結果を維持し、成果物参照の失効を報告する。`pending` なら旧 Task を未解決として依頼者または運用管理者へ通知し、架空の成果物・固定済み payload を作らず、reviewer 側から自動 submit しない。Registry の状態が取得不能または不確定なら未解決として[既存の復旧契約](../herdr-orchestrator/06-results-and-workflows.md#sec-10-2)に従って確認する。未解決の旧 Task と新規依頼の採番継続は別に扱う。ただし旧 Task を完了扱いにせず、同じ Task ID を新規記録へ結び直さない。

世代切替または worktree 再登録の前には、既知の Task ID だけでなく、旧 repository identity と旧 worktree の review Task 全体に未完了 Task がないことを確認する。現行の公開 CLI は Task ID 指定の `task-status` を提供するが、Task 一覧のコマンドはない。Phase 1 の repository 固有 helper は Registry の task JSON を**読み取り専用**で列挙し、既存 schema、所有者、symlink、Task ID、repository identity、worktree、task.type を検証して対象候補を抽出する。各候補を `task-status` でも再確認し、`result.status=pending`、dispatch 不確定、列挙中の新規作成、破損ファイル、アクセス不能、参照できない旧 Registry があれば「未完了なし」と判定しない。Registry を直接書き換えず、列挙の整合性を保証できなければ自動切替を停止する。この列挙は採番の普段の経路には使わず、喪失・再登録時の保守判定に限定する。

世代切替・worktree 再登録と review handoff の競合を避けるため、repository 固有の共有 lock の下で review handoff の開始を一時トークンで登録し、Task ID 発行後に対応付ける。terminal 結果または既存復旧手順による確認まで未完了登録を残し、handoff が Task ID 発行前に失敗した場合も失敗が確定するまで登録を消さない。保存側も記録公開前に同じ lock の下で instance ID、worktree 登録、Task ID の context を再確認する。以前の登録が存在した形跡があり、補助状態とともに lock または未完了登録が消失・破損した場合は実行中の旧 worker を除外できないので自動切替せず停止する。新規採番の再開は、Registry の読み取り専用照合とこの未完了登録の両方で旧 Task の処理がないと確認できた場合に限る。

補助状態だけが失われ、個別記録が残る場合は、旧 instance への新規保存を直ちに禁止する。旧 Task の未完了登録と Registry の全対象 Task を上記の方法で確認し、未完了または不確定な Task があれば自動世代切替を停止する。初期環境・誤削除・破損・初期化中断を同じ「安全な初期状態」と推定しない。安全性を確認できた場合だけ、残る全 instance ディレクトリをその場で新規保存禁止の履歴アーカイブとし、記録の名前と内容を変更・移動しない。これにより、参照元の worktree とファイルが残る限り既存の `result.ref` の相対パスを保つ。アーカイブした instance ID を非公開の `docs/reviews/internal/ARCHIVE_INDEX.md` に記録し、旧 instance への新規保存と旧 Task ID の自動 submit を禁止する。保存前に symlink、重複 ID、不正なパスが見つかれば停止し、黙ってアーカイブ対象から除外しない。

世代初期化は補助状態の削除対象と分離した Git common directory 内の共有 lock で直列化する。初期化 journal に生成済みの新 instance ID、旧 instance ID 一覧、処理段階を原子的に保存し、アーカイブ完了後に active instance を最後に公開する。途中で異常終了した場合は同じ journal と instance ID で再開し、未完了の初期化中は新規 handoff・採番を禁止する。journal が欠ける、内容が矛盾する、同じ instance ID の別内容がある場合は自動再生成せず停止する。完了後、**新規レビュー依頼の handoff より前に**`RV001` から開始する。旧記録は旧 instance ID のまま参照し、新 instance の記録として取り込まない。

補助状態も `docs/reviews/internal/` 配下の個別記録も存在しない新しい環境では、上記の未完了 Task と初期化 journal の確認を満たしたときだけ、**新規レビュー依頼の handoff より前に**新たなランダムなローカル instance ID と採番状態を排他的に初期化し、`RV001` から自動採番する。過去の Task ID を再処理するための初期化には使わない。旧環境に残る Task Registry の terminal result は旧 instance の `result.ref` を保持し、新 instance の `RV001` とはパスが異なる。既存 terminal result の参照切れは解消しないが、別のレビュー記録を誤って参照することは防ぐ。記録と補助状態がともに失われれば過去の ID、Task ID 対応、Result Payload は復元できず、旧 instance の採番連続性も失われる。記録の一意性は instance ID と Review ID の組で扱う。旧 Registry または旧 worktree を確認できず、未完了 Task がないと判断できない場合は、全損であっても自動初期化しない。

worktree の削除・再作成や移設時に補助状態が残っていれば、旧 worktree の実パスを指す。旧パスが実在する間は別 worktree からの採番を拒否する。旧パスが存在せず、新 worktree が同じ Git common directory と repository identity に属する場合も、旧 worktree の未完了登録と Registry の全対象 Task を照合し、未完了・不確定な旧 Task があれば自動再登録しない。安全と判定できる場合だけ共有 lock 下で登録パスを新 worktree へ更新し、欠落記録を台帳に残して次番号へ進む。新 worktree は再登録後に発行した新規 Task だけを扱う。旧 Task ID の Result Payload を新 worktree から再提出できると仮定せず、旧 Task の継続が必要なら Registry に記録された旧 worker と worktree の[context 照合・復旧契約](../herdr-orchestrator/06-results-and-workflows.md#sec-10-3)に従う。旧パスが実在する、identity が異なる、状態が破損している、または安全性を判断できない場合は自動切替しない。旧 Task ID の result locator は変更しない。

<a id="sec-6"></a>

## 6. Result 登録と障害復旧

[Result Contract](../herdr-orchestrator/06-results-and-workflows.md#sec-10)に従い、`result.ref` は元の worktree 内に存在する通常ファイルへの相対パスとする。worktree 内の正本が削除されれば `result.ref` も失効する。Registry の既存 locator は別 worktree を作成しても自動更新されない。この喪失は[§5](#sec-5)の範囲で扱う。

| 事実                     | 確認方法                                  | Task 完了との関係                      |
| ------------------------ | ----------------------------------------- | -------------------------------------- |
| 個別記録保存済み         | worktree 内の存在・内容・ID・台帳ハッシュ | まだ完了ではない                       |
| Registry result terminal | `task-status` と `task-result`            | payload と artifact を照合して報告する |

`result-submit` 直前に保存済み payload を読み、`result.ref` が worktree 内の通常ファイルで、symlink を使わず、台帳のハッシュと一致することを再確認する。保存済みファイルは通常の誤編集を抑える権限にし、再レビューは新記録で行う。submit 失敗または応答消失時は新 Review ID を発行せず、Registry を読み、pending なら既存契約の許す条件下で同一 payload を再提出する。terminal で同じ payload なら replay として扱い、異なれば停止する。dispatch 未確定時の retry と復旧は[既存手順](../herdr-orchestrator/06-results-and-workflows.md#sec-10-2)に従う。

submit 後は `task-result`、記録、台帳を再照合する。現行実装の同一 payload replay は artifact の存在を再確認しないため、replay 成功だけを成果物の健全性の根拠にしない。後続確認が失敗した場合、repository 側の完了報告を保留して人間へ報告する。Registry の terminal result は巻き戻せず、async completion が既に試行された可能性もあるため、復旧と訂正を別途扱う。

repository 側の lock と直前・直後のハッシュ確認は、協調しない外部プロセスによる同時改変を完全には防げない。Phase 1 は不整合の検出と安全停止を保証範囲とし、中央 CLI が成果物の内容ハッシュを原子的に検証すると主張しない。

<a id="sec-7"></a>

## 7. 公開防止と権限

`.inbox/` と `docs/reviews/internal/` はディレクトリ単位で Git 除外する。Git common directory 内の補助状態も Git 管理対象にしない。作成時は実行ユーザーを owner としてディレクトリ 0700、ファイル 0600 とし、アプリ側の追加暗号化は行わない。親ディレクトリ・対象ファイルの owner、symlink、実パスを確認する。既存ファイルの上書き、symlink 経由の許可領域外への書き込みを拒否する。

公開前の自動検査は追跡済みファイルと staged diff の禁止パスを検出し、`git add -f` による強制追加も停止対象とする。別パスに複写された内容は、機密情報らしい文字列を検査し、検出した実値をログへ出さない。Issue、PR、コメント、ブランチ名、コミットメッセージ、差分、CI ログ、添付物は人間が公開前に最終確認する。文脈からの推測可能性は自動検査だけで判断しない。公開可否が不明なら投稿・push を停止する。

CI は push 後の検出であり、公開済み情報の流出防止にはならない。任意の強制追加や検査を飛ばした push を技術的に完全防止できるとも扱わない。GitHub 管理対象レビューの投稿と Finding Issue の権限は[現行レビュー Skill](../../.agents/skills/repo-review/SKILL.md)と[Finding Skill](../../.agents/skills/review-finding/SKILL.md)の規則を維持する。

<a id="sec-8"></a>

## 8. 移行と改修対象

実装承認後は新規依頼だけを新方式へ切り替える。旧 `.review-inbox/` の固定ファイルと履歴は読み取り専用で残し、進行中 task の有無を確認してから旧参照を廃止する。既存の[Scope INDEX](INDEX.md)と過去の検証記録は移動・削除しない。旧・新パスを同時に履歴の正本として更新しない。

改修対象候補は `.agents/skills/repo-review/SKILL.md` と `references.md`、`.agents/skills/repo-workflow/SKILL.md`、repository 固有の記録・採番 helper とテスト、`.gitignore`、依頼・応答テンプレート、`AGENTS.md`、`README.md`、公開可能な運用文書である。旧パス参照を検索し、運用上の参照を切り替える。中央 Orchestrator の Task Registry schema、handoff、result、completion は原則変更しない。

<a id="sec-9"></a>

## 9. 受け入れ検証

実装後は少なくとも次を確認する。実行したテストと未実施の検証を区別して記録する。

1. LIGHT でも2段階レビュー、反証確認、Blocking 判定、既存保証の確認が維持される。
2. 状態遷移、並行処理、権限、セキュリティ変更は適切な深さでレビューされる。
3. 同一 worktree の並列依頼で配送ファイル、Review ID、INDEX が衝突しない。
4. 登録済み worktree が残る間は別 worktree からの採番を拒否する。旧パス消失後も、未完了の旧 Task がないことを確認できた場合だけ同じ Git common directory と repository identity の新 worktree に共有 lock 下で自動再登録し、次番号から再開する。旧 Task の新 worktree からの submit は拒否する。
5. high-water mark 更新直後、個別記録公開直後、台帳更新直後、submit 前、submit 応答消失後の故障注入で番号を再利用しない。
6. 記録が残る同一 Task ID の再処理では同じ Review ID と Result Payload を使用し、依頼・対象差分・summary が異なれば停止する。記録を失った旧 Task ID は再処理しない。
7. 残存する個別記録から INDEX を再生成でき、記録の欠落を台帳に残して次番号へ進める。内容差異や重複は欠落として処理せず停止する。
8. Git 除外ファイルも消す clean・worktree 削除による記録喪失を検出し、欠落した旧 Task ID の replay・submit を拒否する。旧 Task の Registry result が terminal、pending、取得不能の各場合に報告を分け、pending を放置したまま完了扱いにしない。新規 Task ID には採番済み番号を再利用せず次番号を発行できる。
9. 補助状態だけが失われた場合は旧 Task が未完了でないことを読み取り専用の Registry 列挙と未完了登録で確認してから、全残存記録を同じ相対パスでアーカイブし、新 instance の `RV001` を発行する。両方がない新しい環境でも同条件を満たす場合に限って handoff 前に新 instance を作る。未完了・不確定、Registry 列挙不能、破損、同時 handoff では停止する。
10. 世代初期化途中の強制終了から同じ instance ID で再開し、active instance の重複公開や旧 instance への保存を防げる。親記録を失った Follow-up は前回指摘を検証不能として通常の Follow-up GO にしない。
11. `result-submit` 前後でファイルの存在、通常ファイル性、symlink 不使用、ハッシュと Registry result を確認できる。
12. 仮置き依頼は障害時に保持され、確認条件を満たした後だけ削除できる。
13. `RV999` の次を `RV1000` として発行し、Follow-up の Chain と Finding 対応を追跡できる。
14. 強制 Git 追加、権限異常、symlink、公開対象への機密情報混入を検出・停止できる。
15. GitHub PR・Finding Issue の既存権限、中央 Orchestrator の handoff/result/completion 契約に回帰がない。

Orchestrator の回帰確認には `npm run test:orchestrator` と `npm run verify:orchestrator` を使い、Markdown 変更時は `npm run format:md` と `npm run check:md` を通す。実機の並列配送と故障復旧は実装後に別途検証する。

<a id="sec-10"></a>

## 10. 運用上の確定事項

次の7点は運用方針として確定した。

| 項目                 | 決定内容                                                                                                                                |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 保存先               | `docs/reviews/internal/` を非公開レビュー履歴の唯一の正本とする                                                                         |
| バックアップ         | 作成しない。HDD 障害・PC リプレース時の移行忘れによる記録喪失を許容する                                                                 |
| アクセス制御         | 実行ユーザーを owner としてディレクトリ 0700、ファイル 0600 とする。アプリ側の追加暗号化は行わない                                      |
| 運用責任者           | 日常管理と自動回復結果の確認はレビュー実行者。状態破損・別 worktree 登録・廃止判断はリポジトリ管理者が担う                              |
| 全損時の採番         | 記録と補助状態がともにない新しい環境では、未完了 Task がないと確認できた場合、新 instance ID を発行し `RV001` から自動開始する          |
| 一部喪失時の採番     | 記録だけが消えたら欠落 ID を飛ばして継続する。補助状態だけが消えたら、未完了 Task の確認後に残存記録をアーカイブして `RV001` から始める |
| clean・worktree 削除 | 記録喪失を許容し、削除前チェックや削除阻止の仕組みを Phase 1 に追加しない                                                               |

通常運用での保持期間・明示的な廃止手順は、自動アーカイブの設計と一緒に別フェーズで決める。Phase 1 では通常の記録を自動削除・自動アーカイブしない。記録の内容差異や採番台帳の破損、旧 worktree が残る場合の別 worktree 登録要求は、単なる記録喪失として自動処理しない。

補助状態のファイル形式、instance ID の生成規則、high-water mark と台帳の形式、共有 lock と初期化 journal の配置・破損検出は実装着手前の詳細実装設計で定める。特に lock と未完了登録を補助状態と同時に失った場合、自動復旧を許可しない条件を維持する。

バックアップを必須とした第4ラウンドの GO は本改訂へ自動的に引き継がない。Phase 1 の運用選択は確定したが、設計差分の確認と変更対象ファイル・テスト計画の最終化を経て実装承認を受ける。設計書の保存はこの承認を代行しない。
