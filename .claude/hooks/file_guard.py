from __future__ import annotations

from pathlib import Path
from common import decision, read_input

SENSITIVE_NAMES = {".env", "settings.json", ".mcp.json"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
IMPORTANT_PARTS = {
    "migrations", "deploy", "deployment", "production", ".github", "infra", "terraform"
}


def main() -> int:
    payload = read_input()
    raw_path = str(payload.get("tool_input", {}).get("file_path", ""))
    if not raw_path:
        return 0
    path = Path(raw_path)
    lowered_parts = {part.lower() for part in path.parts}

    if path.name.lower().startswith(".env") or path.suffix.lower() in SENSITIVE_SUFFIXES:
        decision("deny", "秘密情報を含む可能性のあるファイルへの書込みを拒否しました。")
        return 0

    if path.name in SENSITIVE_NAMES or lowered_parts.intersection(IMPORTANT_PARTS):
        decision("ask", "設定・CI・マイグレーション・本番関連ファイルの変更は重要事項です。")
        return 0

    decision("allow", "プロジェクト内の通常ファイルへの可逆な編集です。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
