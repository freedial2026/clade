# Claude Code運用実用書

## 1. タスク開始

`/start-task TASK-ID`で、目的・範囲・受入条件・承認点を固定します。タスクが大きい場合は実装前に分割します。

## 2. 調査

`repo_map.py`、`rg`、`git diff`、対象シンボルを優先します。大きなログ、CSV、JSON、生成ファイルを直接読ませません。

## 3. 実装

通常はSonnetの`implementer`、機械処理はHaikuの`mechanical-operator`へ分担します。Pythonスクリプトはdry-runと入力検証を持たせます。

## 4. 検証

変更箇所の単体テストから始め、必要な場合だけ統合テストや全体テストへ広げます。実行できなかった項目は未確認として残します。

## 5. レビュー

`/review-diff`で実際の差分をレビューします。DB、ML、セキュリティ、規約が関係する場合だけ専門Subagentを追加します。

## 6. 完了

`/finish-task`で受入条件、変更ファイル、テスト、残存リスク、次の正確な作業を記録します。commit/push/deployは別承認です。
