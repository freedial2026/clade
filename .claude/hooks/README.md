# Hooks

- `command_guard.py`: Bashコマンドをdeny / ask / allowへ分類する。
- `file_guard.py`: 秘密情報を拒否し、設定・本番・マイグレーション変更だけ承認を求める。
- `record_change.py`: 編集されたファイル名だけをローカル監査ログへ記録する。
- `audit_config.py`: Claude設定・Skills変更をローカル監査ログへ記録する。

Hookは標準入力のJSONを読み、Claude Codeの構造化決定を標準出力へ返します。
パターンは完全なShell解析ではないため、OS権限や保護ブランチの代替にしないでください。
