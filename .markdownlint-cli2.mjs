/** @type {import("markdownlint-cli2").Configuration} */
export default {
  // リポジトリ内の Markdown のみ（`**/*.md` だと node_modules 配下も拾う）
  globs: ["AGENTS.md", "docs/**/*.md"],
  config: {
    default: true,
    // 日本語の長文・ワイドな表は Prettier と併用し、行長はここでは enforce しない
    MD013: false,
    // 章・節アンカー（sec-N / sec-N-M）用の <a id="..."></a>（AGENTS.md 文書管理ルール）
    MD033: {
      allowed_elements: ["a"],
    },
    // 手順書内の **ラベル** 見出し（本当の見出し階層にはしない）
    MD036: false,
  },
};
