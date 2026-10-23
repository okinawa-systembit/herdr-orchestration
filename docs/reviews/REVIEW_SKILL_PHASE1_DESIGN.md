# 内部レビュースキル改善 Phase 1 設計書

> 状態: Phase 1 設計承認済み（GO）。Issue #64 にて実装中。
>
> 対象: このリポジトリの内部レビュー。比較対象の固有情報は記載しない。

本書は [Issue #61](https://github.com/okinawa-systembit/herdr-orchestration/issues/61) および [Issue #64](https://github.com/okinawa-systembit/herdr-orchestration/issues/64) の計画、Issue コメント 6 の障害発生時のデータ喪失許容と再起動後の正常稼働方針、および R61-01〜R61-13 の設計対応を、このリポジトリ固有の仕様としてまとめる。中央 Orchestrator の共通 Task Contract、状態遷移、Result Contract、`task-list` 仕様は既存の[設計書目次](../herdr-orchestrator-design.md)から参照し、本書では複製しない。

## 目次

- [1. 適用範囲と承認状態](#sec-1)
- [2. 正本と識別子](#sec-2)
- [3. 配送とレビュー実行](#sec-3)
- [4. 履歴の保存と採番](#sec-4)
- [5. 保持範囲と障害後の扱い](#sec-5)
- [6. Result 登録と障害復旧](#sec-6)
- [7. 公開防止と権限](#sec-7)
- [8. 移行と改修対象](#sec-8)
- [9. 受け入れ検証](#sec-9)
- [10. 運用上の確定事項](#sec-10)

<a id="sec-1"></a>

## 1. 適用範囲と承認状態

Phase 1 はこのリポジトリの Herdr 内部レビューの品質基準、配送ファイル、非公開履歴を対象とする。比較対象のリポジトリ、中央 Orchestrator の共通スキーマ、GitHub PR と Finding Issue の管理方式は変更しない。

バックアップを必須とする技術設計は Issue #61 の第4ラウンド再レビューで一度 GO と判定されたが、その後の運用判断および Issue コメント 6 により方針を抜本的に見直した。データの完全な復旧よりも、障害後に PC / WSL を再起動し、人間が再び操作した際、以前の中途半端な状態に妨げられず正常に作業できることを優先する。

### 設計原則

> 「消えたデータは諦めてよい。クラッシュ前の作業を復元する必要もない。ただし、PC / WSLの再起動後に、人間が再び操作したとき、以前の中途半端な状態が原因で正常な作業を開始できなくなることは避ける。この原則を満たす範囲で、実装をできるだけ単純に保つ。」

### 想定する障害

次の事象を同一の障害カテゴリとして扱い、障害の発生原因ごとの個別復旧機構は設けず、再起動後の安全な稼働を共通の要件とする。

- WSL 内のプロセスが OOM 等で異常終了する。
- WSL 自体が異常終了する。
- Windows が強制終了する、または BSOD 等で停止する。
- PC の電源断が発生する。
- 処理実行中に Windows / WSL が終了する。

### スコープ外（要求しないこと）

次の仕組みは過剰設計として Phase 1 の要件から除外する。

- クラッシュ前の作業状態の完全復元
- すべてのレビュー履歴の永続化
- 未完了 Task の自動再開・自動再配送
- 失われた成果物の再生成
- Task Registry 全体の自動修復
- 複雑な journal / rollback / replay 機構
- 世代切替の自動化、履歴の自動再構築
- Registry の独自全件走査
- Windows / WSL の障害原因の自動診断

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

| 情報                               | 正本                                                              | ID・参照                                                                  |
| ---------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Task、dispatch、result、completion | [Task Registry](../herdr-orchestrator/03-task-registry.md#sec-6)  | Registry が発行する Task ID                                               |
| 配送前の依頼                       | Git 除外した `.inbox/`                                            | 一時パストークン (`review-request.<token>.md`)                            |
| 内部レビューの個別記録             | worktree 内で Git 除外した `docs/reviews/internal/<instance-id>/` | Review ID、初回 Review ID を再利用する Chain ID                           |
| 内部レビューの一覧                 | `docs/reviews/internal/<instance-id>/INDEX.md`                    | 個別記録から再構築できる派生索引                                          |
| 採番と排他制御の補助状態           | Git common directory 内の非公開状態領域                           | 共有 lock (`flock`、固定パス)、instance ID、worktree 実パス、採番カウンタ |
| Scope と GitHub Issue の対応       | 既存の [docs/reviews/INDEX.md](INDEX.md) と GitHub                | 既存の R001〜R014、I001〜I005                                             |
| PR レビュー・Finding Issue         | GitHub                                                            | 既存の番号、権限、クローズ条件                                            |

内部レビューには `RV001`、`RV002`、…、`RV1000` のように最小3桁で表示する Review ID を使用し、上限は設けない。Scope ID の `R001` と区別する。初回レビューの Chain ID はその Review ID と同じ値とし、Follow-up は新しい Task ID と Review ID を取得して元の Chain ID と直前の Review ID を記録する。共通 Task Registry に Review ID を追加しない。

個別記録にはローカルの repository instance ID、Task ID、Review ID、Chain ID、Follow-up 元、レビュー対象の commit または未コミット差分の識別、前回指摘ごとの対応状態、依頼内容のハッシュ、判定、実施・未実施の検証、固定した Result Payload を保存する。`result.status=succeeded` はレビュー作業の結果登録を表し、レビュー判定の GO を意味しない。

<a id="sec-3"></a>

## 3. 配送とレビュー実行

現行の `handoff` は Task ID を内部で発行してから worker に通知する。公開 CLI に Task ID の事前発行機能はないため、requester が事前に Task ID 命名のファイルを直接作る方式は採らない。Phase 1 の review handoff は必ず repository 固有 helper を経由し、[handoff の契約](../herdr-orchestrator/04-handoff-and-requester.md#sec-7)に従い、次の順序にする。

1. requester は helper（`scripts/repo-review-helper request-create`）を通じて[§4 の共有 lock](#sec-4)下で登録 worktree を確認し、一時ファイルに依頼内容を書き込んでから不可逆な rename / link 等で `.inbox/review-request.<random-token>.md` として原子的に公開する。短い相対パスを `handoff --instruction` に載せる。一時パストークンは配送前の衝突回避用で、Task ID や Review ID の代わりに使用しない。`--instruction` はファイル内容を取り込む機能ではない。
2. worker は通知された Task ID で `task-status` を読み、Registry の instruction、worker、repository identity、worktree を照合する。一致しなければ依頼を読んだだけで作業を開始せず、既存の[worker 受信手順](../../skills/herdr-orchestrator/references/worker-receive.md)に従う。
3. worker は公開された依頼書（`.inbox/review-request.<token>.md`）を読み、[repo-review Skill](../../.agents/skills/repo-review/SKILL.md) の2段階レビュー、指摘の反証確認、既存保証の確認を実施する。Tier は調査範囲を決めるが品質ゲートの省略には使わない（※ 依頼ファイルを別ディレクトリへ写す中間工程は採らず、公開ファイルを直接参照する）。
4. [§4](#sec-4)〜[§6](#sec-6) に従って記録・result 登録を行う。requester は sync handoff の終了だけを信用せず、`task-result` と記録を確認する。

仮置き依頼は Registry の terminal result、個別記録と `result.ref` の有効性を確認した後にのみ削除できる。公開前の一時ファイル（書き込み途中の未完成ファイル）は、異常終了後に破棄または隔離してよい。一方、正常に公開済みで Task ID が発行されなかった handoff 失敗や、dispatch/result が不確定な依頼は自動削除せず、手動で確認する。作成後30日の経過は手動確認の目印とし、依頼者または運用管理者が再依頼の有無を確認する。`uncertain` を未配送と断定して自動再送しない。

<a id="sec-4"></a>

## 4. 履歴の保存と採番

Phase 1 の採番名前空間はローカルの Git common directory を共有する repository instance ごととし、登録した単一 worktree からの保存のみ許可する。Git common directory 内の Git 管理対象外の非公開状態領域に共有 lock、ローカル instance ID、登録 worktree の実パス、採番用カウンタ（high-water mark）を保持する。個別記録は `docs/reviews/internal/<instance-id>/RVNNN.md` に保存し、`result.ref` にも instance ID を含める。

個別記録がレビュー履歴の正本であり、INDEX と補助状態は単独では履歴の正本にならない。保存処理は次の原則を守る。

1. **排他制御と固定境界**: 共有 lock には `flock` などのプロセス終了時に OS により確実に解放される仕組みを利用する。lock ファイルの識別先は instance ID に依存させず、Git common directory 内の固定パス（例: `herdr-review.lock`）とし、再初期化の前後を問わず同一の排他境界を維持する。lock ファイルは切り離し・再初期化の対象外とし、使い捨てにせず、削除・再作成による別 inode への分岐を防止する。初期化処理、補助状態の読み書き、採番、個別記録公開の全行程をこの共有 lock の保護下で直列化する。lock 取得後は `fstat(fd)` と `stat(path)` の照合などにより有効な同一 inode を確認する（照合の詳細は詳細実装設計で定める）。前回の異常終了でロックが残留し、永久に処理不能になる事態を防止する。
2. **原子的公開**: 個別記録は一時ファイルに書き込み、永続化（fsync）後に不可逆な rename 等で原子的に公開する。書き込み途中の不完全なファイルを正常な完成データとして扱わない。
3. **衝突防止と欠番許容**: 次番号は `max(個別記録の既存番号, 採番カウンタ) + 1` とし、カウンタを原子的に更新する。クラッシュにより番号が飛んでも欠番として許容し、番号の重複発行を防止する。
4. **正本からの派生更新**: INDEX は個別記録から派生して更新する。INDEX が欠落または不整合な場合は、残存する個別記録から再生成する。

同一 Task ID の再処理では、個別記録が既に存在していれば既存の Review ID と Result Payload をバイト単位で再利用し、本文を上書きしない。親記録が失われた Follow-up では前回指摘の対応を検証不能と明記し、通常の Follow-up として GO にしない。必要な範囲を再調査した新規の初回レビューを新しい Chain ID で開始する。既存の[Finding Issue 管理](../../.agents/skills/review-finding/SKILL.md)は変更しない。

補助状態が破損または消失した場合は、古い管理状態を安全に切り離し、[§5.5](#sec-5-5) に従って共有 lock 下で安全性を確認した上で新しい instance ID で初期化する。過去の履歴復元は要求しない。

<a id="sec-5"></a>

## 5. 保持範囲と障害後の扱い

### 5.1 データ喪失の許容

内部レビューの本文と固定済み Result Payload は `docs/reviews/internal/<instance-id>/` の個別記録に保存する。バックアップや別媒体への複製は作成しない。以下の喪失・破損は運用上許容する。

- 処理途中のレビュー依頼・結果
- 保存済みのローカルレビュー履歴
- Review ID の採番状態
- 補助的な管理ファイル
- 未完了 Task の成果物

`git clean -fdx` / `git clean -fdX`、worktree 削除、HDD 障害、PC リプレース時の移行忘れなどによる記録喪失を許容し、Phase 1 では削除前チェックや削除阻止の仕組みを実装しない。失われたデータの自動復元、履歴の完全性保証、過去の処理の継続実行は要求しない。必要なレビューは新規 Task として再実行すればよい。

### 5.2 残存 Task の確認（`task-list` の活用）

Registry ディレクトリの独自全件走査は行わない。残存 Task やクラッシュ前の状態確認には、Orchestrator 共通コマンドである `herdr-orchestrator task-list`（[task-list 設計書](../herdr-orchestrator-task-list.md)）を利用する。

残存 Task の確認にあたっては、このリポジトリの review Task に絞って実行する（例: `--repository <identity> --type review`、必要に応じて `--worktree <path>`）。Agent および人間は `task-list` を実行し、次の点を確認する。

- `complete`、`truncated`、`concurrent_changes` の状態。`truncated=true` または `complete=false` のときは、一覧に対象が見当たらない場合でも「残存 Task なし」と断定してはならない（詳細は [task-list 設計書](../herdr-orchestrator-task-list.md#34-ソート順と件数制限)）。
- `overview.state`（`uncertain` / `open` / `closed`）および `overview.warnings`。
- 残存 Task の一覧と状態（特に `result.status=pending` や不確定な Task）。

### 5.3 不確定な旧 Task の扱い

障害発生前の Task について、以下は行わない。

- 自動再配送
- 自動再実行
- 状態不明の Task を成功扱いにすること
- Registry の状態を根拠なく変更すること
- 不完全な成果物の自動復元

旧 Task の復元が困難または不確定な場合は、その処理を安全に放棄し、新しい Task ID で新規レビューを依頼する。

### 5.4 障害後の操作フロー

PC / WSL の異常終了や再起動後の標準フローは次のとおりとする。

1. Windows / WSL を再起動する。
2. 人間がエージェントに作業再開または新規レビューを指示する。
3. 必要に応じて `task-list` 等で残存 Task を確認する。
4. 未完了・不確定な旧 Task について、再配送や重複実行を行わないよう確認する。
5. 旧 Task の復元が困難な場合は、その処理を放棄する。
6. 不完全な一時ファイルや補助状態を新規処理から切り離す。
7. 新しい Task としてレビューを開始する。

旧状態の修復が完了するまで新規作業を永久にブロックする設計は避ける。

<a id="sec-5-5"></a>

### 5.5 補助状態の破損・喪失と再初期化

複雑な journal 管理や自動アーカイブ世代交代の多段ロールバックは採用しない。

補助状態が存在しない新規環境、または状態ファイルの形式不正・解析不能などの破損を検出した場合の再初期化は、[§4 の固定共有 lock](#sec-4) の保護下で次の手順に従う。lock ファイル自体は切り離しの対象外として維持する。目的は旧 Task の復元ではなく、旧 Task と新 Task の誤った混在や重複実行を防ぐことである。

1. **旧 Task の混在防止と確認**: 補助状態の破損と旧 Task の停止は同義ではない。再初期化前に、可能な範囲で `task-list`（`--repository <identity> --type review`）および worker の稼働状態を確認する。
2. **実行中・不確定時の自動開始抑止**: 旧 Task が worker 上で実行中である可能性がある場合、または状態を確定できない場合（`truncated=true`、`complete=false`、あるいは旧 Task の実行有無が不明な場合）は、自動的な新 instance 発行やレビュー開始を行わず安全に停止する。人間に旧 Task の状況（完了待ち、停止、または放棄）を確認させる。
3. **安全な放棄と新規開始**: 人間による明示的な操作（helper の再初期化オプションの指定等。操作仕様は詳細実装設計で定める）により旧 Task の放棄が確認された場合、または旧 Task が実行中でないことが確認できた場合に限り、古い管理状態を安全に切り離し、新しいランダムなローカル instance ID を発行して `RV001` から新規に採番を開始する。旧 Task の状態を勝手に変更しないため、放棄された旧 Task は Registry 上でそのまま残るが、新旧の記録は instance ID（ディレクトリ）で明確に分離される。過去の記録が存在していても、旧 instance ID の配下に残るためパスが衝突することはない。過去の採番との連続性復元は要求しない。
4. **セキュリティ疑義時の安全停止**: ファイルやディレクトリの所有者・パーミッション異常などセキュリティ上の疑義がある場合は、勝手な自動初期化を行わず安全に停止し、原因と必要な手動操作（権限修正等）をユーザーへ明示する。

worktree の再作成や移設時に補助状態が残っている場合、旧 worktree のパスが存在せず、かつ未完了の旧 Task がないことを `task-list` で確認できた場合に限り、登録パスを新 worktree へ更新できる。安全性を確認できない場合は新しい instance ID で再初期化するか、リポジトリ管理者が手動で判断する。旧 Task ID の成果物を新 worktree から再提出できるとは仮定せず、必要なら新規 Task として依頼する。

補助状態の具体的なファイル形式、instance ID の生成規則、lock ファイルの検証・照合手順、破損検出の詳細判定は実装着手前の詳細実装設計で定める。Registry の独自全件走査や複雑な自動復旧機構は追加しない。

<a id="sec-6"></a>

## 6. Result 登録と障害復旧

[Result Contract](../herdr-orchestrator/06-results-and-workflows.md#sec-10)に従い、`result.ref` は元の worktree 内に存在する通常ファイルへの相対パスとする。worktree 内の正本が削除されれば `result.ref` も失効する。この喪失は[§5](#sec-5)の範囲で許容する。

| 事実                     | 確認方法                       | Task 完了との関係                      |
| ------------------------ | ------------------------------ | -------------------------------------- |
| 個別記録保存済み         | worktree 内の存在・内容・ID    | まだ完了ではない                       |
| Registry result terminal | `task-status` と `task-result` | payload と artifact を照合して報告する |

`result-submit` 直前に保存済み payload を読み、`result.ref` が worktree 内の通常ファイルで、symlink を使わないことを確認する。保存済みファイルは通常の誤編集を抑える権限（0600）とし、再レビューは新記録で行う。submit 失敗または応答消失時は、Registry を読み、`pending` なら同一 payload を再提出する。`terminal` で同じ payload なら replay として扱い、異なれば停止する。dispatch 未確定時の retry と復旧は[既存手順](../herdr-orchestrator/06-results-and-workflows.md#sec-10-2)に従う。

submit 後は `task-result` と記録を照合する。不完全な結果ファイルを正常扱いせず、完了報告を保留して人間へ報告する。

<a id="sec-7"></a>

## 7. 公開防止と権限

`.inbox/` と `docs/reviews/internal/` はディレクトリ単位で Git 除外する。Git common directory 内の補助状態も Git 管理対象にしない。作成時は実行ユーザーを owner としてディレクトリ 0700、ファイル 0600 とし、アプリ側の追加暗号化は行わない。親ディレクトリ・対象ファイルの owner、symlink、実パスを確認する。既存ファイルの上書き、symlink 経由の許可領域外への書き込みを拒否する。

公開前の自動検査は追跡済みファイルと staged diff の禁止パスを検出し、`git add -f` による強制追加も停止対象とする。別パスに複写された内容は、機密情報らしい文字列を検査し、検出した実値をログへ出さない。Issue、PR、コメント、ブランチ名、コミットメッセージ、差分、CI ログ、添付物は人間が公開前に最終確認する。文脈からの推測可能性は自動検査だけで判断しない。公開可否が不明なら投稿・push を停止する。

CI は push 後の検出であり、公開済み情報の流出防止にはならない。任意の強制追加や検査を飛ばした push を技術的に完全防止できるとも扱わない。GitHub 管理対象レビューの投稿と Finding Issue の権限は[現行レビュー Skill](../../.agents/skills/repo-review/SKILL.md)と[Finding Skill](../../.agents/skills/review-finding/SKILL.md)の規則を維持する。

<a id="sec-8"></a>

## 8. 移行と改修対象

実装承認後は新規依頼だけを新方式へ切り替える。旧 `.review-inbox/` の固定ファイルと履歴は読み取り専用で残し、進行中 task の有無を確認してから旧参照を廃止する。既存の[Scope INDEX](INDEX.md)と過去の検証記録は移動・削除しない。旧・新パスを同時に履歴の正本として更新しない。

改修対象候補は `.agents/skills/repo-review/SKILL.md` と `references.md`、`.agents/skills/repo-workflow/SKILL.md`、repository 固有の handoff・記録・採番 helper とテスト、`.gitignore`、依頼・応答テンプレート、`AGENTS.md`、`README.md`、公開可能な運用文書である。Skill と運用文書には review handoff の helper 経由を必須として記載する。旧パス参照を検索し、運用上の参照を切り替える。中央 Orchestrator の Task Registry schema、handoff、result、completion、`task-list` は変更しない。

<a id="sec-9"></a>

## 9. 受け入れ検証

実装後は少なくとも次を確認する。実行したテストと未実施の検証を区別して記録する。

### 障害耐性と運用継続性（Issue コメント 6 要件）

1. レビュー依頼ファイルの作成途中で異常終了しても、古い一時ファイルに妨げられず新規レビューを開始できる。
2. レビュー結果ファイルの保存途中で異常終了しても、不完全な結果を正常な完成データとして扱わない（原子的公開の確認）。
3. Task 配送途中で異常終了しても、旧 Task を誤って自動重複配送・再実行しない。
4. 排他処理中にプロセスが終了しても、次回起動時に永久ロック状態にならない。共有 lock の識別先は instance ID に依存せず、同一 Git common directory 内で一意かつ安定した固定境界を維持し、再初期化前後でも直列化される（`flock` による自動解放と同一 lock 境界の確認）。
5. 採番状態が破損・喪失しても、旧 Task の実行中混在を防止し、状態不確定時は人間に確認を求めた上で、ID 衝突を起こさず新しい instance ID 等の管理状態で安全に再開できる。
6. 古い一時ファイルや不完全な補助状態が、新規処理を恒久的に妨げない（破棄・隔離・新規採番の確認）。
7. 旧 Task を復元できない場合でも、安全な放棄・新規依頼の手順が存在し、Orchestrator 共通の `task-list` コマンドで残存状態を確認できる。
8. 過去のレビュー履歴を復元しなくても、通常運用へ戻れる（欠番の許容と履歴喪失の許容）。

### レビュー品質と運用要件

1. LIGHT でも2段階レビュー、反証確認、Blocking 判定、既存保証の確認が維持される。
2. 状態遷移、並行処理、権限、セキュリティ変更は適切な深さでレビューされる。
3. 同一 worktree の並列依頼で配送ファイル、Review ID、INDEX が衝突しない。
4. 登録済み worktree が残る間は別 worktree からの採番を拒否する。
5. `RV999` の次を `RV1000` として発行し、Follow-up の Chain と Finding 対応を追跡できる。
6. 親記録を失った Follow-up は前回指摘を検証不能として通常の Follow-up GO にしない。
7. 強制 Git 追加、権限異常、symlink、公開対象への機密情報混入を検出・停止できる。
8. GitHub PR・Finding Issue の既存権限、中央 Orchestrator の handoff/result/completion 契約に回帰がない。

Orchestrator の回帰確認には `npm run test:orchestrator` と `npm run verify:orchestrator` を使い、Markdown 変更時は `npm run format:md` と `npm run check:md` を通す。実機の並列配送と故障復旧は実装後に別途検証する。

<a id="sec-10"></a>

## 10. 運用上の確定事項

次の点は運用方針として確定した。

| 項目                   | 決定内容                                                                                                                                                                                                                                     |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 保存先                 | `docs/reviews/internal/` を非公開レビュー履歴の唯一の正本とする                                                                                                                                                                              |
| バックアップ           | 作成しない。HDD 障害・PC リプレース時の移行忘れによる記録喪失を許容する                                                                                                                                                                      |
| 障害時の復旧原則       | クラッシュ前の作業復元は不要。PC / WSL 再起動後に正常な新規作業を開始できることを最優先とし、実装を極小・単純に保つ                                                                                                                          |
| 状態確認               | Orchestrator 共通の `task-list` を利用し、残存 Task や状態不整合を確認する。独自の全件走査は実装しない                                                                                                                                       |
| アクセス制御           | 実行ユーザーを owner としてディレクトリ 0700、ファイル 0600 とする。アプリ側の追加暗号化は行わない                                                                                                                                           |
| 排他制御               | instance ID に依存しない Git common directory 単位の固定 lock ファイルを使用し、同一境界で初期化・採番・保存を保護。プロセス終了時に OS により自動解放される `flock` を使用し、永久ロックを防止する                                          |
| 運用責任者             | 日常管理と結果確認はレビュー実行者。自動初期化で解消しない状態異常（権限異常等）・別 worktree 登録・廃止判断はリポジトリ管理者が担う                                                                                                         |
| 破損・喪失時の再初期化 | 補助状態が不在または解析不能な場合、`task-list` 等で旧 Task の実行可能性を確認。実行中または状態不確定時は自動初期化せず人間に確認し、放棄確認後に新 instance ID を発行して `RV001` から新規開始する。権限異常等は停止して手動操作を案内する |
| clean・worktree 削除   | 記録喪失を許容し、削除前チェックや削除阻止の仕組みを Phase 1 に追加しない                                                                                                                                                                    |

通常運用での保持期間・明示的な廃止手順は、自動アーカイブの設計と一緒に別フェーズで決める。Phase 1 では通常の記録を自動削除・自動アーカイブしない。補助状態の具体的なファイル形式、instance ID の生成規則、破損検出の詳細判定は実装着手前の詳細実装設計で定める。
