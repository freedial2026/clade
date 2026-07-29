# Claude Code 完成版プロジェクトテンプレート v2.0

Claude Codeを、**低トークン・Python優先・重要事項だけ承認**で運用するための実務テンプレートです。
初期プロファイルとして「公営競技の統計予測システム」を収録していますが、`docs/PROJECT_PROFILE.md`と`tasks/`を差し替えれば他プロジェクトにも再利用できます。

## 現在の状態(ボートレース予測ライブラリ)

`tasks/BACKLOG.md`のP0〜P3、全26タスクを実装済みです。

```text
src/boat_prediction/    P0-P3のライブラリ本体(下記参照)
tests/                  287件のユニット/統合テスト
docs/local-setup.md      ローカル環境セットアップ手順
```

**重要:** すべて合成データ(テスト用フィクスチャ)のみで構築・検証されています。
実際の公式レースデータは一切投入されていません。本番・実運用の前に、`tasks/HANDOFF.md`
末尾の手順(実データ取得 → P0再実行 → P2フォワードテストの安定確認 → 昇格の別途承認)
を必ず行ってください。

主なモジュール:

- `race_id.py` / `temporal.py` / `reconstruction.py` — 正規レースキー、時点管理、時点復元
- `inventory.py` / `ingest.py` / `validation.py` / `integrity.py` / `quarantine.py` / `quality.py` — P0データ監査パイプライン
- `baseline.py` / `walk_forward.py` / `feature_availability.py` / `model_comparison.py` / `calibration.py` / `model_registry.py` — P1予測基盤
- `odds.py` / `market.py` / `expected_value.py` / `abstention.py` / `paper_simulation.py` / `stability.py` — P2市場比較・ペーパー運用
- `entry_course.py` / `second_place.py` / `exacta.py` / `exacta_paper_operation.py` — P3進入予測・2連単

詳細と各タスクの決定事項・既知のリスクは`tasks/P0-T001.md`〜`tasks/P3-T004.md`を参照してください。

## 最初に行うこと

1. ZIPをプロジェクトルートへ展開する。
2. `python scripts/validate_template.py`を実行する。
3. `docs/PROJECT_PROFILE.md`を実案件に合わせて編集する。
4. `.claude/settings.local.example.json`を`.claude/settings.local.json`へコピーし、個人設定を調整する。
5. Claude Codeを起動し、`/project-start`を実行する。

## 設計原則

- ルート`CLAUDE.md`は短く保ち、常時読み込む情報を限定する。
- 機械的処理は、モデルで繰り返さずPythonスクリプトへ移す。
- Haikuは探索・整形・定型処理、Sonnetは通常実装、Opusは高難度設計・高リスク判断に限定する。
- ファイル読取、検索、テスト、内部編集など可逆な作業は原則承認不要とする。
- 本番、破壊、課金、公開、認証、個人情報、規約、自動購入などは承認を求める。
- 1タスク1目的、最小差分、テスト、記録を徹底する。

## 主な構成

```text
CLAUDE.md                         常時読み込む最小ルール
.claude/settings.json            共有権限・Hooks
.claude/agents/                  専門Subagents
.claude/skills/                  再利用ワークフロー
.claude/hooks/                   決定論的な安全制御
.claude/rules/                   詳細ルール
.claude/templates/               成果物テンプレート
docs/expert-review/              14領域レビュー
docs/domain/                     ボートレース予測設計実用書
scripts/                         Python優先の機械操作
tasks/                           タスク状態とP0～P3計画
profiles/                        汎用・案件別プロファイル
```

## 重要

- `.claude/settings.json`の共有`allow`ルールは、Claude Code側のWorkspace Trust対象です。
- `.claude/settings.local.json`はGit管理しません。
- Hooksは補助制御です。OS権限、Git保護、本番権限、バックアップの代替ではありません。
- 予測・分析システムは利益を保証しません。初期版に自動購入機能を含めません。

詳細は`docs/00_START_HERE.md`を参照してください。
