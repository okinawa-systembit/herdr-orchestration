---
name: design-doc-markdown-format
description: >-
  Formats and lints Markdown after creating or editing Herdr Orchestrator design
  documents. Use when changing AGENTS.md, docs/herdr-orchestrator-design.md,
  docs/herdr-orchestrator/*.md, docs/herdr-agent-review-guidelines.md, or when
  the user mentions 設計書, design doc, or Markdown formatting in this repo.
---

# Design doc Markdown format

Herdr Orchestrator の設計書を**作成・変更・修正したら、作業完了前に必ず** Markdown の整形と lint を通す。

文書構造・アンカー・目次などの編集ルールは [AGENTS.md](../../../AGENTS.md) に従う。

## 対象ファイル

- [AGENTS.md](../../../AGENTS.md)
- [docs/herdr-orchestrator-design.md](../../../docs/herdr-orchestrator-design.md)
- [docs/herdr-orchestrator/*.md](../../../docs/herdr-orchestrator/)
- [docs/herdr-agent-review-guidelines.md](../../../docs/herdr-agent-review-guidelines.md)

## 手順

リポジトリルート（`package.json` があるディレクトリ）で実行する。

1. 内容の編集を終える（章番号・アンカー `sec-N` / `sec-N-M`、目次リンクなど）。
2. 整形する: `npm run format:md`
3. 確認する: `npm run check:md`（Prettier + markdownlint-cli2）
4. `check:md` が失敗したら原因を直し、2–3 を繰り返す。**成功するまでタスク完了にしない。**

`node_modules` が無い場合は先に `npm ci` する（依存の新規追加時以外は `npm install` を使わない）。

## 触ったファイルだけ直す場合

全体の `format:md` で問題ない。変更ファイルのみに限定する場合:

```bash
npx prettier --write --ignore-path .prettierignore -- path/to/file.md
npm run check:md
```

## 設定の参照

- Prettier: [prettier.config.mjs](../../../prettier.config.mjs)（`proseWrap: preserve`）
- markdownlint: [.markdownlint-cli2.mjs](../../../.markdownlint-cli2.mjs)

## 完了チェック

- [ ] 設計書の Markdown を編集した
- [ ] `npm run format:md` を実行した
- [ ] `npm run check:md` が exit 0 である
