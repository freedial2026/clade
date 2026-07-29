# はじめに

このテンプレートは、Claude Codeへ巨大な設計書を毎回読み込ませず、**必要なルールだけ常時読込み、詳細はSkills・Subagents・タスクファイルからオンデマンドで取得する**構成です。

## 推奨開始手順

```bash
python scripts/validate_template.py
python scripts/repo_map.py --max-depth 3
claude
```

Claude Code内：

```text
/project-start
/start-task P0-T001
```

## 承認の考え方

通常の読取、検索、Python処理、テスト、内部編集は進めます。本番・破壊・公開・課金・認証・個人情報・規約・Git共有履歴などだけ承認を求めます。詳細は`docs/approval-matrix.md`です。

## モデルの考え方

機械処理はHaiku、通常実装はSonnet、Opusは重大な設計判断だけに限定します。詳細は`docs/model-token-policy.md`です。
