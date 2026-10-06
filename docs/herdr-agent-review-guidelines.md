# Herdr Orchestrator エージェントレビューガイドライン

## 1. 目的

本資料は、Herdr Orchestrator に関する設計書および実装を AI エージェントがレビューする際の共通基準を定める。

レビューでは、単に「動作しそうか」を確認するのではなく、Herdr 本来の設計思想・公式仕様・実運用上の安全性・コミュニティで蓄積された知見との整合性を確認する。

対象は主に次のとおり。

- Herdr Orchestrator 設計書
- Orchestrator Skill
- wrapper CLI
- Task Registry
- Agent 間 handoff / completion
- routing / state management
- repository 固有 Skill との責務分離
- テスト仕様

---

## 2. 最重要レビュー原則

Herdr Orchestrator は、Herdr 公式が提供する機能・操作面・責務を尊重して設計する。

機能が存在することだけを採用理由とせず、その機能の公式な目的、保証範囲、現在の一般的な使用方法を確認する。

公式仕様で不足する領域のみ独自 Contract を設計し、その場合もコミュニティの実装・Issue・Discussion 等と比較し、安全性、単純性、運用性を優先する。

レビューでは以下の優先順位で根拠を確認する。

1. 現在利用している Herdr バージョンの実際の CLI / API / schema
2. 対応する Herdr 公式 Skill
3. Herdr 公式ドキュメント
4. Herdr 公式リポジトリの Issue / Discussion / 実装
5. コミュニティで利用されている実装・運用例
6. 独自設計

下位の情報が上位の仕様と矛盾する場合、原則として上位を優先する。

---

## 3. 機能の存在と本来の用途を区別する

「Herdr にその機能、ID、フィールド、API が存在する」ことと、「その用途への利用が公式に想定または保証されている」ことを同一視しない。

レビュー時は、採用している Herdr 機能ごとに少なくとも次を確認する。

- 本来何のための機能か
- 公式ドキュメントまたは実装上どの用途で使用されているか
- routing / identity / session / terminal location 等の意味を取り違えていないか
- 値の寿命や再利用可能性はどうなっているか
- その用途で安定性が保証されているか
- コミュニティではどのように扱われているか
- より本来の用途に合う公式機能が存在しないか

例として、session identity、pane location、terminal identity、live Agent alias 等を同じ「Agent ID」として扱わない。

用途外利用を採用する場合は、なぜ必要なのか、公式手段ではなぜ不足するのか、どのリスクを受容するのかを設計書に明記すること。

---

## 4. 公式に存在しない保証を作らない

Herdr が保証していない性質を、Orchestrator の設計上あたかも Herdr が保証しているように扱わない。

特に以下を確認する。

- immutable な Agent identity が本当に存在するか
- routing target が永続的に同一 Agent を意味するか
- timeout / stalled が未配送を意味するか
- pane / terminal / session の再利用や occupant 変更があり得るか
- Agent lifecycle の状態が task の業務状態を意味するか

公式に保証が存在しない場合は、制約として明記し、安全側に設計する。

独自の ID や Contract を導入する場合、それを Herdr ネイティブの保証と混同しない。

---

## 5. Official-first、独自実装は必要最小限とする

Herdr 自体が提供している操作を独自実装で再発明しない。

Agent が Herdr を操作する際の規範的な手順は Herdr 公式 Skill を優先する。

Orchestrator wrapper が Herdr CLI / API を直接使用する場合は、公式 Skill を runtime library として呼び出すのではなく、公式仕様に従って決定的な処理を実行するものとする。

wrapper 側でも機械的に検証可能な公式前提条件を確認すること。

例:

- Herdr 環境であること
- 対象 Agent が解決可能であること
- Agent state が操作可能な状態であること
- 利用する CLI / API が現在の Herdr バージョンでサポートされていること

仕様が不明な場合に推測で CLI syntax や内部挙動を固定しない。

---

## 6. コミュニティのベストプラクティスと比較する

独自設計を採用する前に、Herdr コミュニティで同じ問題がどのように扱われているか確認する。

対象には以下を含める。

- Herdr GitHub Issues
- Herdr GitHub Discussions
- 公開されている orchestration 実装
- Agent automation の実運用例
- state / callback / durable result の管理方法

ただし、コミュニティ実装を無条件で正解とはしない。

以下を確認して採用可否を判断する。

- 複数の実装で共通しているか
- Herdr 公式思想と矛盾しないか
- 特定バージョン向け workaround ではないか
- undocumented behavior に依存していないか
- 現在の Herdr でも必要な対策か

単一の実装例だけを根拠に標準仕様へ取り込まない。

---

## 7. Fail Closed を基本とする

routing target、repository、worktree、task context 等を安全に確認できない場合、推測して処理を続行しない。

原則:

> 誤配送するくらいなら配送しない。

特に、保存済み locator が利用できない場合に別 locator へ自動 fallback しない。

例:

- Agent name が見つからないため pane ID へ fallback
- pane が変わったため terminal ID から別 Agent を推測
- session ID から requester を推測

このような fallback は、明示的に安全性が証明されない限り採用しない。

context mismatch、target 不在、検証不能は明確な状態として記録する。

---

## 8. 「失敗」と「結果不明」を区別する

Herdr 操作の結果を二値の success / failed だけで扱わない。

特に Herdr の timeout / stalled 等について、公式仕様上「入力が届かなかった」ことが保証されない場合は `uncertain` として扱う。

原則:

- 確実に配送されなかった → failed / skipped
- 配送されたことを確認できた → sent
- 配送されたか判断できない → uncertain

`uncertain` を自動的に再送しない。

blind retry によって同じ依頼を二重実行する危険があるため、人間または明示的 recovery 操作による確認を要求する。

この原則は初回 dispatch と completion / callback の両方に適用する。

---

## 9. Agent lifecycle と Task lifecycle を分離する

Herdr Agent の状態と Orchestrator Task の状態を混同しない。

Herdr lifecycle の例:

```text
idle / working / blocked / done / unknown
```

これは Agent の現在状態であり、業務 task の成功・失敗を直接意味しない。

Orchestrator は独立した Task Registry と state machine を持つ。

少なくとも以下の軸を別々に扱う。

```text
task.status
dispatch.status
result.status
completion.status
```

レビューでは状態名だけでなく、イベントごとの許可遷移、禁止遷移、異常時遷移、manual recovery を確認する。

---

## 10. unknown / uncertain は後続の確定情報で解消できるようにする

不明状態を永久的なエラー扱いにしない。

例えば dispatch が `uncertain` でも、その後 worker から正規の task result が届いた場合、配送履歴としての `dispatch.status=uncertain` は保持しつつ、task の最終状態は result に従って確定できる設計とする。

履歴的事実と現在の task 状態を混同しない。

一方で、確定済みの履歴を後から都合よく書き換えない。

---

## 11. Task continuity を Agent process continuity より優先する

非同期処理では、同じ Agent プロセスが最後まで存在し続けることを前提にしない。

Orchestrator が保証すべき中心は以下とする。

```text
Task continuity + Work context continuity
```

requester Agent が交代していても、同じ論理 role と安全に確認された作業 context を引き継いでいる場合は、workflow の要件に応じて completion を受け取れる設計を許容する。

最低限確認する context の例:

- repository identity
- worktree path

必要に応じて追加確認する例:

- branch
- commit
- Agent kind
- cwd / foreground cwd

完全に同じ Agent instance であることを証明できない場合、それを保証すると記述しない。

---

## 12. Task Registry を durable source of truth とする

非同期 task の復元に requester の chat history を必須としない。

Task Registry には少なくとも以下を保持する。

- task identity
- task type
- task instruction
- repository / worktree context
- requester metadata
- worker metadata
- task / dispatch / result / completion state
- result reference
- resume-oriented result summary

completion prompt は詳細な結果そのものではなく、requester を再開させる trigger とする。

原則:

```text
completion prompt = resume trigger
Task Registry = durable task context source of truth
worktree result = detailed result source of truth
```

---

## 13. Task Registry は Single Writer とする

Task Registry の JSON を Agent や repository Skill が直接編集しない。

Registry writer を一つに限定し、public Orchestrator CLI を経由して更新する。

Registry writer は以下を保証する。

- schema validation
- state transition validation
- file locking
- temporary file を使用した atomic replace
- duplicate completion の抑止
- path validation
- file permission

同じ状態を複数の Agent が任意に書き換えられる設計を認めない。

---

## 14. Canonical Schema を一つにする

同じ意味の値に複数の名前や表現を導入しない。

例:

```text
task.id
```

を canonical とした場合、Registry 内で `task_id` 等の別表現を混在させない。

設計書、CLI、Task Registry、completion prompt、テストで同じ schema を使用する。

schema version を持ち、未対応 version を推測して処理しない。

---

## 15. sync / async で不要な二重設計を作らない

sync と async は workflow の実行方法が異なるだけで、可能な限り同じ task model、schema、Task Registry、result contract を利用する。

不要に別々の状態管理方式を作らない。

差異は completion action 等、実際に異なる部分だけに限定する。

---

## 16. Result Contract を明確にする

worker の自然言語応答だけを task 完了判定に使用しない。

worker から Orchestrator へ提出する result は構造化し、task identity と関連付ける。

少なくとも以下を明確にする。

- task ID
- result status
- result reference
- result summary

artifact が存在する場合、その詳細結果を durable な場所へ保存する。

result reference が null を許可する状態と、必須となる状態を明確にする。

---

## 17. Repository / Worktree の意味を混同しない

論理的 repository identity と filesystem 上の worktree path を別物として扱う。

- repository identity: Git repository を識別する論理値
- worktree path: task が実際に操作している checkout / worktree の絶対 path

result artifact の path 解決には実際の worktree path を使用する。

Git common directory や別 worktree を暗黙の path base としない。

---

## 18. Path Safety を確認する

Task Registry や result artifact の path を外部入力から組み立てる場合、安全な範囲から脱出できないことを確認する。

result reference が worktree-relative である場合、少なくとも以下を拒否する。

- absolute path
- `..` による traversal
- `~`
- symlink を経由した worktree 外への escape

Task Registry についても symlink、owner、permission、registry root 外への escape を確認する。

---

## 19. 人間に異常を隠さない

人間が requester Agent に「レビュー依頼して」「テスト依頼して」等を指示した場合、dispatch が成立しなければ requester は同じ対話で失敗を伝える。

Orchestrator が `dispatched` を返す前に「依頼しました」と報告しない。

配送結果が uncertain の場合は、未配送と断定せず、重複防止のため自動再送していないことを明示する。

正常完了時の追加 push 通知を中央 Orchestrator が必ず提供する必要はない。

ただし、自動化が異常終了した事実を人間から隠さない。

---

## 20. 中央 Orchestrator と repository 固有処理を分離する

中央 Orchestrator は task の意味そのものを実装しない。

中央側の責務例:

- logical role
- routing
- handoff
- Task Registry
- sync / async policy
- completion
- context validation
- Result Contract
- approval minimization

repository 側の責務例:

- review scope
- lint / build / test
- repository 固有の review policy
- artifact naming
- business-specific status
- project-specific security checks

中央 Orchestrator に repository 固有ルールを蓄積しない。

---

## 21. Repository を変更できることを前提にしない

中央 orchestration の利用条件として、各 repository への以下の変更を必須にしない。

- 中央 Skill のコピー
- symlink
- 共通 script の埋め込み
- repository 管理者権限

repository に独自 Skill が存在する場合は共存できること。

中央管理は repository 外で成立することを基本とする。

---

## 22. Approval を減らすために安全性を下げない

日常的な handoff で不要な approval prompt を減らすことは重要だが、そのために arbitrary shell execution 等を許可しない。

固定された public CLI と限定された引数を使用する。

例:

```text
handoff
result-submit
task-status
task-result
resume-task
doctor
```

明示的 approval を要求すべき例:

- destructive Git operation
- force terminate / delete
- arbitrary shell
- arbitrary file delete
- sandbox / permission 変更
- arbitrary send-keys
- blocked Agent への回答

「承認を減らす」と「権限を広げる」を同一視しない。

---

## 23. Runtime dependency を必要最小限にする

標準環境で実現可能な処理に追加 dependency を導入しない。

Python 標準ライブラリで十分な task ID、JSON、file lock、atomic update 等のために外部 package を要求しない。

追加 dependency を採用する場合は以下を確認する。

- 標準機能では不足する理由
- installation / update / security の運用負担
- WSL / Linux 環境への影響
- Agent ごとのセットアップ増加

---

## 24. MVP と将来拡張を分離する

便利そうな機能を理由なく MVP へ追加しない。

レビューでは各機能について以下を確認する。

- 現在の目的達成に必要か
- 将来安全に追加できるか
- 今追加すると state / schema / recovery が複雑化しないか
- Herdr 本体で将来提供される可能性のある機能を先回りして再実装していないか

将来候補は設計上の extension point として残し、MVP の必須仕様と混同しない。

---

## 25. 設計書と実装の整合性を確認する

実装レビューでは、コード単体の妥当性だけでなく設計書との一致を確認する。

特に以下を照合する。

- public CLI 名
- Canonical Schema
- state machine
- error classification
- uncertain handling
- retry policy
- routing policy
- context validation
- Result Contract
- file permission
- cleanup policy
- manual recovery

実装が合理的でも設計書と異なる場合、どちらを修正すべきかを指摘する。

無断で「実装の方が正しい」として仕様を変更しない。

---

## 26. レビュー時の調査方法

Herdr の仕様に関する判断が必要な場合は、記憶だけで断定せず現在の情報を確認する。

優先して確認するもの:

- installed `herdr --help`
- installed `herdr api schema`
- Herdr 公式 Skill
- Herdr 公式 documentation
- Herdr 公式 GitHub repository
- 関連 Issue / Discussion
- 現在利用している Herdr version

公開情報と installed behavior が異なる場合、実際に対象環境で利用する version の挙動を重視し、差異をレビュー結果へ記載する。

コミュニティ情報を根拠にする場合は、公式仕様とコミュニティ慣行を区別して記述する。

---

## 27. Blocking 判定

次のような問題は原則として implementation / release blocking とする。

- Herdr 公式仕様と明確に矛盾する
- undocumented behavior を必須前提としている
- routing の誤配送につながる
- uncertain を failed と誤判定して自動再送する
- state machine が未定義または矛盾している
- Task Registry の競合で state が破壊され得る
- task/result の対応関係を保証できない
- path traversal / symlink escape が可能
- repository / worktree を誤認する可能性がある
- Canonical Schema が複数存在する
- recovery 不能な状態が仕様上発生する
- 設計書と実装の重要な Contract が一致しない

軽微な文言、可読性、将来改善等は non-blocking として分離する。

---

## 28. レビュー出力形式

レビュー結果は、少なくとも次の形式で整理する。

### 総評

設計または実装が、次工程へ進める状態かを簡潔に記述する。

### Blocking issues

各指摘について以下を記載する。

1. **指摘内容**
2. **該当箇所**
3. **問題となる理由**
4. **Herdr 公式仕様との関係**
5. **コミュニティ / ベストプラクティスとの比較**
6. **推奨する修正方針**
7. **blocking と判断する理由**

### Non-blocking issues

軽微な改善、可読性、運用上の改善候補を記載する。

### 仕様確認事項

Herdr の現在仕様を確認しなければ判断できないものを、推測で結論付けず列挙する。

### 良好な点

重要な設計判断が公式仕様や安全原則に沿っている場合は、その根拠を簡潔に記載する。

---

## 29. レビューで避けること

以下のレビューを行わない。

- 「Herdr に機能があるから使えばよい」という判断
- 公式用途を確認せず ID や metadata を routing identity に転用する
- timeout を未配送と断定する
- uncertain task を無条件に再実行する
- 安全性の問題を「運用でカバー」とだけして閉じる
- コミュニティの単一実装を公式仕様のように扱う
- repository を自由に変更できる前提を置く
- Agent の chat history だけを durable state とする
- reviewer の自然言語だけで task 完了を判定する
- 実装都合で Canonical Schema を増やす
- Herdr lifecycle と business task status を混同する
- 将来機能を理由なく MVP に追加する
- 根拠を確認せず「ベストプラクティス」と表現する

---

## 30. 最終確認

レビュー完了前に、最低限次を確認する。

- Herdr 公式推奨に原則従っているか
- 使用している Herdr 機能は本来の用途に沿っているか
- 公式にない保証を独自に仮定していないか
- コミュニティの実装・議論と比較したか
- 独自実装は公式機能で不足する領域だけか
- routing failure 時に fail closed になっているか
- failed と uncertain を区別しているか
- blind retry がないか
- Agent lifecycle と Task lifecycle が分離されているか
- Task Registry が durable source of truth になっているか
- Registry が single writer になっているか
- state machine が全イベントについて定義されているか
- Canonical Schema が一つに統一されているか
- repository identity と worktree path を区別しているか
- result / Registry の path safety が確保されているか
- sync / async の不要な二重実装がないか
- 異常が人間から隠されないか
- 中央 Orchestrator と repository 固有責務が分離されているか
- repository 変更を必須前提としていないか
- approval 削減のために権限を広げていないか
- runtime dependency が必要最小限か
- MVP と将来拡張が分離されているか
- 設計書と実装の Contract が一致しているか

重大な項目が未確認の場合、「問題なし」と結論付けず、未確認事項として明示すること。
