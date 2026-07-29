from __future__ import annotations

import re
from common import decision, read_input

DENY_PATTERNS = [
    r"(^|[;&|]\s*)rm\s+-rf\s+/(?:\s|$)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*[dx][a-zA-Z]*\b",
    r"\bterraform\s+destroy\b",
    r"\bdocker\s+system\s+prune\b.*\s-a\b",
    r"\bkubectl\s+delete\s+namespace\b",
    r"\bDROP\s+DATABASE\b",
    r"\bTRUNCATE\s+TABLE\b",
]

ASK_PATTERNS = [
    r"\bgit\s+(push|commit|merge|rebase|tag)\b",
    r"\b(alembic|django-admin|manage\.py)\s+(upgrade|downgrade|migrate)\b",
    r"\b(terraform|pulumi)\s+(apply|up)\b",
    r"\bkubectl\s+(apply|delete|rollout)\b",
    r"\b(npm|pnpm|yarn|twine)\s+publish\b",
    r"\b(deploy|release)\b",
    r"\b(prod|production)\b",
]

SAFE_PATTERNS = [
    r"^(python|python3)(\s|$)",
    r"^(pytest|ruff|mypy)(\s|$)",
    r"^git\s+(status|diff|log|show|branch|grep)(\s|$)",
    r"^(rg|find|ls|wc|head|tail)(\s|$)",
]


def main() -> int:
    payload = read_input()
    command = str(payload.get("tool_input", {}).get("command", "")).strip()
    if not command:
        return 0

    for pattern in DENY_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            decision("deny", "破壊的または復旧困難なコマンドをプロジェクトHookが拒否しました。")
            return 0

    for pattern in ASK_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            decision("ask", "本番・公開・履歴変更・マイグレーション等の重要操作のため承認が必要です。")
            return 0

    for pattern in SAFE_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            decision("allow", "読取・検証・Python機械処理など可逆なローカル操作です。")
            return 0

    # No output: normal Claude Code permission flow applies.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
