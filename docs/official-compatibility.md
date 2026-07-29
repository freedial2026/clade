# Claude Code公式仕様との対応

確認日: 2026-07-29

- 共有設定: `.claude/settings.json`
- 個人設定: `.claude/settings.local.json`
- プロジェクトメモリ: `CLAUDE.md`または`.claude/CLAUDE.md`
- Skills: `.claude/skills/<name>/SKILL.md`
- Subagents: `.claude/agents/*.md`
- Hooks: `settings.json`の`hooks`と実行スクリプト
- MCP: `.mcp.json`

公式資料:

- https://code.claude.com/docs/en/claude-directory
- https://code.claude.com/docs/en/settings
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/slash-commands
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/best-practices

モデル名は固定IDではなく`haiku`、`sonnet`、`opus`のエイリアスを使用し、将来のモデル更新に追従しやすくしています。
