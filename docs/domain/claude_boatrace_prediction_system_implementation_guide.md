# Claude Code向け設計実用書
## ボートレース統計予測・検証システム

- 文書種別: 実装設計書 / 開発指示書 / 受入基準書
- 想定実装者: Claude Code
- 想定レビュー者: プロダクト責任者、データエンジニア、機械学習エンジニア、統計担当、セキュリティ担当
- バージョン: 1.1
- 作成日: 2026-07-29
- 初期対象: BOAT RACEの過去データを用いた研究・分析・仮想購入検証

---

# 0. Claude Codeへの最上位指示

この文書を唯一の実装基準として扱うこと。

以下を厳守すること。

1. 自動購入機能は実装しない。
2. 利益を保証する表示・ロジックを実装しない。
3. P0が完了するまで、機械学習モデルを本実装しない。
4. 過去時点で利用できなかった情報を特徴量へ混入させない。
5. Webサイトへの大量アクセスを前提とする取得処理を作らない。
6. 公式ダウンロード、許諾済みデータ、手動CSVを優先する。
7. 予測精度より、データ再現性・監査可能性・確率校正を優先する。
8. 3連単モデルはP0〜P3の受入完了後まで実装しない。
9. 実装変更時は、必ず仕様・マイグレーション・テスト・変更履歴を同時更新する。
10. 不明なデータ仕様は推測して実装せず、`docs/open-issues.md`へ記録する。
11. ファイル検索、置換、集計、形式変換、テスト実行、差分確認などの機械的作業は、会話で大量の内容を読み書きせずPythonまたはシェルスクリプトを優先する。
12. Claudeの高性能モデルを常用しない。低コストモデルで完了できる作業は低コストモデルへ委任し、昇格条件を満たす場合のみ上位モデルを使用する。
13. 重要事項以外はユーザー承認を求めず、既存設計・受入基準・安全な既定値に従って自律的に進める。
14. 承認待ちを理由に作業を停止しない。非重要事項は実装し、判断内容を作業報告へ記録する。
15. 破壊的変更、費用発生、外部公開、認証情報、法務・規約、データ消失、モデル本番昇格などの重要事項は実行前承認を必須とする。


## 0.1 トークン・モデル・機械操作の最適化方針

Claude Codeは、トークン消費量と処理費用を抑えるため、次の順序で作業すること。

```text
1. Pythonまたはシェルで機械処理
2. 既存コード・設定・テスト結果の必要箇所だけを取得
3. 低コストモデルで定型作業
4. 標準モデルで通常実装・レビュー
5. 上位モデルは重要判断に限定
```

### Pythonを優先する作業

以下は、原則としてClaudeが全文を読み込んで手作業せず、Pythonまたはシェルで処理する。

- ファイル名・ディレクトリ構成の一覧化
- 複数ファイルの検索、置換、追記
- CSV・JSON・ログの集計と変換
- DBスキーマやマイグレーションの機械比較
- テスト結果、lint結果、型検査結果の抽出
- 大量データの欠損、重複、型、範囲検査
- ハッシュ計算、差分作成、重複排除
- 定型コードの生成、ファイル分割、命名変換
- バックテスト集計と評価指標計算
- ドキュメント内のリンク・見出し・タスクID検査

Pythonで処理した結果は、全出力を会話へ貼らず、要約・異常・変更対象だけをClaudeへ渡す。

### モデル選択ルール

利用可能なモデルの名称に依存せず、能力階層で判断する。

| 階層 | 主な用途 |
|---|---|
| 低コストモデル | 検索結果の分類、定型修正、単純なテスト追加、文書整形、機械処理結果の要約 |
| 標準モデル | 通常の実装、バグ修正、API・DB・テスト設計、限定されたコードレビュー |
| 上位モデル | アーキテクチャ変更、未来情報混入監査、統計設計、セキュリティ、重大障害、複数案の最終判断 |

上位モデルへ昇格できるのは、次のいずれかに該当する場合だけとする。

- データ消失または後方互換性破壊の可能性がある
- 未来情報混入や評価漏洩の判断が必要
- 認証・権限・秘密情報・外部公開に関係する
- DB設計または主要アーキテクチャを変更する
- 標準モデルで2回解決できなかった
- 複数モジュールにまたがる原因不明の障害
- 統計的妥当性や本番モデル採否を判断する

単純作業を上位モデルで繰り返してはならない。

### コンテキスト削減ルール

- リポジトリ全体を毎回読み直さない。
- `git diff`、対象ファイル、失敗テスト、関連仕様だけを読む。
- 長いログはPythonでエラー、警告、末尾、該当スタックだけ抽出する。
- 既に確定した仕様を会話内で再生成せず、ファイルパスを参照する。
- 1タスク1目的を原則とし、無関係なリファクタリングを同時に行わない。
- 作業結果は「変更点・テスト・未解決・重要判断」に限定して報告する。

## 0.2 承認不要・承認必須の境界

Claude Codeは、重要事項以外について逐次承認を求めず、自律的に実装する。

### 承認不要

次の作業は、既存仕様と受入条件の範囲内であれば承認不要とする。

- 新規ファイルの追加
- 既存コードの内部リファクタリング
- 命名、型、コメント、ログ、例外処理の改善
- 単体・統合・契約テストの追加または修正
- lint、format、型検査への対応
- 非破壊的なDBインデックス追加
- 開発用fixture、factory、seedの追加
- ドキュメント、README、変更履歴の更新
- 明白な不具合修正
- 依存関係を増やさない小規模な性能改善
- 既存API契約を変えない内部実装変更
- 作業に必要なPythonスクリプトやCLIの作成
- 不足ディレクトリ、設定例、テンプレートの補完

判断に迷う軽微事項は、最も単純で可逆性の高い方法を採用し、報告書に記載する。

### 実行前承認が必要な重要事項

- 本番または共有環境のデータ削除・上書き
- 後方互換性を壊すAPI・DB・ファイル形式変更
- 本番DBへのマイグレーション適用
- 有料サービス、クラウド資源、外部APIの契約・課金
- 外部公開、デプロイ、DNS、ドメイン、公開URL変更
- Gitへのpush、merge、release、タグ作成
- 認証情報、秘密鍵、APIキー、個人情報の取扱変更
- 利用規約や法令判断を伴うデータ取得方法
- 自動購入、投票、決済、入出金に接続する変更
- モデルを検証環境から本番へ昇格する操作
- セキュリティ水準を下げる変更
- 主要技術スタックまたはDB製品の変更
- 受入基準、スコープ、予算、納期を変える判断

### 承認が得られない場合

重要事項に到達した場合は、その操作だけを保留し、次を実行する。

1. 実装可能な非破壊部分を完成させる。
2. dry-run、差分、移行手順、ロールバック手順を用意する。
3. 承認が必要な理由と影響範囲を1件にまとめて報告する。
4. 承認待ちを理由に、無関係な作業まで停止しない。

---

# 1. プロジェクト目的

本システムは、レース結果を断定的に予言するものではない。

公開・許諾済みデータから以下を実行する分析基盤である。

- 過去レースの時点再現
- 各艇の1着確率推定
- 進入コース確率推定
- 予測確率の校正
- 市場オッズとの比較
- 条件不良レースの見送り判定
- 固定金額による仮想購入検証
- モデル・データ・判定根拠の監査

初期の成功条件は「利益が出ること」ではない。

次の状態を成功とする。

- 過去レースを再現可能
- 未来情報の混入がない
- 予測確率が保存・説明可能
- 同一入力で同一結果を再現可能
- 見送りを含む仮想運用を継続評価可能

---

# 2. スコープ

## 2.1 初期スコープ

- 全国24場
- 1レース6艇
- 過去番組表
- 過去競走成績
- 選手期別成績
- 開催・場情報
- 1着確率
- 確率校正
- 時系列バックテスト
- 固定100円の仮想購入
- CSV入出力
- 管理画面
- データ品質レポート

## 2.2 P2以降の追加スコープ

- オッズスナップショット
- 市場暗黙確率
- 保守期待値
- 見送り判定
- 進入予測
- 2連単確率

## 2.3 対象外

- 投票サイトとの接続
- 自動購入
- 入出金
- 購入代行
- 予想販売
- 外部ユーザー課金
- 3連単の直接120クラス分類
- Transformer
- リアルタイム分散処理
- モバイルアプリ
- 公営競技横断対応

---

# 3. 設計原則

## 3.1 予測と購入判断の分離

予測モデルは、各結果の確率だけを返す。

購入判断は別サービスで行う。

```text
Prediction Service
  └─ 各艇の1着確率

Decision Service
  ├─ データ品質
  ├─ 確率校正誤差
  ├─ 市場確率
  ├─ オッズ変動
  ├─ 不確実性控除
  └─ 見送り判定
```

モデル内部に「買う・買わない」を埋め込まないこと。

## 3.2 時点整合性

すべての特徴量は、予測日時以前に利用可能でなければならない。

必須条件:

```sql
available_at <= prediction_at
```

## 3.3 再現可能性

各予測に以下を保存する。

- model_version_id
- feature_set_version_id
- dataset_snapshot_id
- prediction_at
- source_hash
- code_commit_sha
- random_seed
- environment_version

## 3.4 単純モデル優先

基準モデルを必ず作成する。

- 枠番だけの多項ロジスティック回帰
- 枠番＋選手基礎成績
- LightGBM
- CatBoost

複雑なモデルは、基準モデルを時系列検証で上回る場合のみ採用する。

---

# 4. 推奨技術構成

## 4.1 初期構成

| 領域 | 技術 |
|---|---|
| OS | Debian 13 または Ubuntu LTS |
| 実行環境 | Docker Compose |
| 言語 | Python 3.12 |
| API | FastAPI |
| DB | PostgreSQL 17 |
| ORM | SQLAlchemy 2.x |
| Migration | Alembic |
| Batch | Python CLI + cron |
| ML | scikit-learn / LightGBM / CatBoost |
| Data | pandas / Polarsのいずれか1つ |
| Test | pytest |
| Validation | Pydantic |
| UI | FastAPI Templates + HTMX または簡易Next.js |
| Experiment metadata | PostgreSQL |
| File storage | ローカルボリューム、後にS3互換へ移行可能 |

## 4.2 初期導入しないもの

- Celery
- Redis
- Kafka
- Kubernetes
- Grafana
- Airflow
- Spark
- MLflow Server

必要性が計測されるまで追加しない。

---

# 5. リポジトリ構成

```text
boat-forecast/
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── Makefile
├── alembic.ini
├── app/
│   ├── main.py
│   ├── config.py
│   ├── db/
│   │   ├── session.py
│   │   ├── models/
│   │   └── repositories/
│   ├── domains/
│   │   ├── races/
│   │   ├── racers/
│   │   ├── ingestion/
│   │   ├── quality/
│   │   ├── features/
│   │   ├── prediction/
│   │   ├── calibration/
│   │   ├── odds/
│   │   ├── decision/
│   │   └── backtest/
│   ├── api/
│   ├── cli/
│   ├── templates/
│   └── static/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/
├── data/
│   ├── raw/
│   ├── staged/
│   ├── rejected/
│   └── exports/
├── models/
├── notebooks/
│   └── research_only/
├── docs/
│   ├── architecture.md
│   ├── data-dictionary.md
│   ├── source-register.md
│   ├── model-card.md
│   ├── operations.md
│   ├── open-issues.md
│   └── adr/
└── scripts/
```

`notebooks/`の処理を本番コードから呼び出してはならない。

---

# 6. データソース管理

## 6.1 データソース優先順位

1. 公式ダウンロードファイル
2. 契約・許諾済みAPI
3. 管理者によるCSVアップロード
4. 規約確認済みの低頻度取得

## 6.2 ソース登録項目

`data_sources`テーブルを作成する。

```text
id
code
name
provider
source_type
official_url
terms_url
acquisition_method
update_frequency
license_note
is_active
last_verified_at
created_at
updated_at
```

## 6.3 取得禁止事項

- ページを秒単位で巡回しない
- robots.txtやサイトポリシーを無視しない
- CAPTCHA回避を実装しない
- 非公開APIを解析して利用しない
- 認証回避を行わない
- 負荷試験を外部公式サイトへ実行しない

---

# 7. データモデル

## 7.1 ID規則

レースの自然キー:

```text
race_date + venue_code + race_number
```

内部IDはUUID v7を使用する。

## 7.2 主要テーブル

### venues

```text
id
code
name
timezone
is_active
created_at
updated_at
```

### race_meetings

```text
id
venue_id
meeting_start_date
meeting_end_date
meeting_title
grade
source_id
created_at
updated_at
```

### races

```text
id
meeting_id
race_date
race_number
distance_meters
scheduled_deadline_at
actual_deadline_at
status
is_fixed_entry
created_at
updated_at
UNIQUE(race_date, venue_id, race_number)
```

### racers

```text
id
registration_number
name
branch
birth_date
sex
created_at
updated_at
UNIQUE(registration_number)
```

### racer_term_stats

```text
id
racer_id
term_code
class
win_rate
second_place_rate
third_place_rate
average_st
valid_from
valid_to
published_at
available_at
source_id
```

### motors

```text
id
venue_id
motor_number
service_period_start
service_period_end
created_at
updated_at
UNIQUE(venue_id, motor_number, service_period_start)
```

### boats

```text
id
venue_id
boat_number
service_period_start
service_period_end
created_at
updated_at
```

### race_entries

```text
id
race_id
lane_number
racer_id
motor_id
boat_id
listed_class
listed_weight
listed_national_win_rate
listed_local_win_rate
listed_average_st
listed_motor_second_rate
listed_boat_second_rate
source_published_at
available_at
created_at
updated_at
UNIQUE(race_id, lane_number)
CHECK(lane_number BETWEEN 1 AND 6)
```

### race_conditions

```text
id
race_id
weather
wind_direction
wind_speed_mps
wave_height_cm
air_temperature_c
water_temperature_c
stabilizer_used
lap_shortened
observed_at
published_at
available_at
```

### exhibition_entries

```text
id
race_entry_id
exhibition_course
exhibition_time_sec
exhibition_st_sec
tilt_angle
parts_replacement_json
observed_at
published_at
available_at
```

### race_results

```text
id
race_id
confirmed_at
winning_method
is_refund
source_id
created_at
```

### race_result_entries

```text
id
race_result_id
race_entry_id
actual_course
actual_st_sec
finish_position
status
```

### odds_snapshots

```text
id
race_id
bet_type
combination
odds
observed_at
available_at
source_id
UNIQUE(race_id, bet_type, combination, observed_at)
```

### dataset_snapshots

```text
id
name
cutoff_at
source_manifest_json
row_counts_json
content_hash
created_at
```

### model_versions

```text
id
name
algorithm
hyperparameters_json
training_period_start
training_period_end
calibration_method
metrics_json
artifact_path
code_commit_sha
created_at
status
```

### predictions

```text
id
race_id
model_version_id
dataset_snapshot_id
feature_set_version
prediction_at
probabilities_json
confidence_score
data_quality_score
source_hash
created_at
UNIQUE(race_id, model_version_id, prediction_at)
```

### prediction_decisions

```text
id
prediction_id
decision_type
bet_type
combination
model_probability
conservative_probability
market_probability
odds
expected_value
recommended
skip_reason_codes_json
created_at
```

### virtual_bets

```text
id
prediction_decision_id
stake_yen
placed_at
settled_at
payout_yen
profit_yen
status
```

---

# 8. データ時点管理

各データは最低でも以下を区別する。

- `event_time`: 現実の事象発生時刻
- `published_at`: 提供元が公開した時刻
- `collected_at`: 自システムが取得した時刻
- `available_at`: 予測に利用可能になった時刻
- `valid_from`: 統計値の有効開始
- `valid_to`: 統計値の有効終了

## 8.1 未来情報混入防止テスト

予測用クエリでは必ず以下を満たすこと。

```sql
WHERE available_at <= :prediction_at
```

テストで意図的に未来データを登録し、取得されないことを確認する。

## 8.2 スナップショット

学習・検証ごとにデータセットを固定する。

スナップショットには以下を保存する。

- ソースファイル一覧
- SHA-256
- 行数
- 期間
- 除外件数
- 欠損件数
- 作成コードのGit SHA

---

# 9. 取込処理

## 9.1 取込パイプライン

```text
RAW
 ↓
ファイル検証
 ↓
文字コード・圧縮形式判定
 ↓
STAGED
 ↓
構文解析
 ↓
正規化
 ↓
業務ルール検証
 ↓
DB UPSERT
 ↓
品質レポート
```

## 9.2 冪等性

同一ファイルを複数回取り込んでも重複を作らない。

必須:

- source_file_hash
- parser_version
- ingestion_run_id
- unique constraint
- upsert

## 9.3 エラー分類

```text
E001 ファイル破損
E002 文字コード不明
E003 必須列欠落
E004 レース識別不能
E005 艇番範囲外
E006 選手番号不正
E007 重複
E008 時刻矛盾
E009 モーター期間矛盾
E010 結果不整合
```

不正行は捨てず、`data/rejected/`と`ingestion_errors`へ保存する。

---

# 10. データ品質

## 10.1 品質軸

- Completeness
- Uniqueness
- Validity
- Consistency
- Timeliness
- Point-in-time integrity

## 10.2 品質スコア

```text
完全性       25点
一意性       15点
妥当性       20点
整合性       15点
時点整合性   25点
合計        100点
```

判定:

| 点数 | 扱い |
|---:|---|
| 95〜100 | 学習・予測可能 |
| 90〜94 | 予測可能、警告表示 |
| 80〜89 | 研究のみ |
| 0〜79 | 予測停止 |

## 10.3 必須品質ルール

- 1レースの出走艇数は原則6
- 艇番は1〜6で一意
- 1着は原則1艇
- finish_position重複を検査
- 1着確率合計は1.0±1e-6
- 予測時刻より未来のデータを使用しない
- モーター使用期間外参照を禁止
- 結果確定前に結果由来特徴量を参照しない

---

# 11. 特徴量設計

## 11.1 Feature Set V1

初期版は過剰な特徴量を禁止する。

### レース内基本特徴

- lane_number
- listed_class
- listed_national_win_rate
- listed_local_win_rate
- listed_average_st
- listed_motor_second_rate
- listed_boat_second_rate

### 相対特徴

- win_rate_minus_race_mean
- local_win_rate_minus_race_mean
- average_st_minus_race_mean
- motor_rate_minus_race_mean
- lane_adjusted_win_rate

### 直近特徴

過去時点に存在する結果のみから算出する。

- last_5_average_finish
- last_10_average_finish
- last_10_average_st
- last_30d_win_rate
- last_90d_win_rate
- finish_std_last_10

### 会場特徴

- venue_code
- lane_venue_historical_win_rate
- racer_venue_historical_win_rate

## 11.2 欠損値

- 欠損を0で埋めない
- 欠損フラグを作る
- 学習パイプライン内で処理する
- 推論時も同じ変換器を使用する

## 11.3 特徴量禁止事項

- 確定着順
- 確定払戻
- 確定後更新された勝率
- 未来節成績
- 締切後オッズ
- 結果公開時刻以後に得た情報

---

# 12. モデル設計

## 12.1 目的変数

1着艇を6クラスで予測する。

```text
y ∈ {1, 2, 3, 4, 5, 6}
```

出力確率:

```text
P1 + P2 + P3 + P4 + P5 + P6 = 1
```

## 12.2 基準モデル

### Baseline A

枠番別過去1着率。

### Baseline B

多項ロジスティック回帰。

特徴:

- lane_number
- class
- national_win_rate
- average_st

### Candidate C

LightGBM multiclass。

### Candidate D

CatBoost multiclass。

## 12.3 採用条件

候補モデルは以下を満たす場合のみ採用する。

- 未使用期間のLog LossでBaseline Bを改善
- Brier Scoreが悪化しない
- Calibration Errorが許容範囲
- 月別性能が極端に不安定でない
- 特定場だけで性能が出ていない
- 再学習で再現可能

## 12.4 確率校正

学習データと校正データを分ける。

候補:

- sigmoid
- isotonic
- temperature scaling

校正手法は検証期間のLog LossとECEで選択する。

同じデータでモデル学習と校正を行わない。

## 12.5 アンサンブル

固定比率は禁止。

次を比較する。

1. 最良単体モデル
2. 単純平均
3. 検証期間Log Loss最小化重み

StackingはP3以降。

---

# 13. 時系列検証

## 13.1 禁止

`train_test_split(shuffle=True)`は禁止。

## 13.2 基本分割

データ期間に応じて相対指定する。

例:

```text
Train       最初の60%
Calibration 次の15%
Validation  次の15%
Test        最後の10%
```

Testはモデル選択に使用しない。

## 13.3 ウォークフォワード

```text
学習12か月 → 翌1か月評価
1か月移動
学習12か月 → 翌1か月評価
```

最低12ウィンドウを目標とする。

## 13.4 評価指標

### 予測

- Multiclass Log Loss
- Multiclass Brier Score
- Top-1 Accuracy
- Top-2 Accuracy
- Expected Calibration Error
- Reliability Diagram

### 安定性

- 月別Log Loss
- 場別Log Loss
- グレード別Log Loss
- 期間別Calibration Error
- 特徴量ドリフト

### 仮想購入

- 件数
- 的中率
- 投資額
- 払戻額
- 回収率
- 最大連敗
- 最大ドローダウン
- 月別損益
- 場別損益

回収率だけでモデル採否を決めない。

---

# 14. オッズ・市場比較

P2で実装する。

## 14.1 市場暗黙確率

各組み合わせについて:

```text
raw_probability_i = 1 / odds_i
market_probability_i = raw_probability_i / Σ raw_probability
```

## 14.2 オッズ時刻

以下を混同しない。

- observed_at
- prediction_at
- decision_at
- deadline_at
- result_confirmed_at

バックテストでは、購入判断時点に観測済みのオッズだけを使用する。

## 14.3 保守確率

```text
conservative_probability
= max(0, calibrated_probability - uncertainty_margin)
```

`uncertainty_margin`は固定値で決めず、校正誤差・標本数・モデル分散から算出する。

## 14.4 保守期待値

```text
conservative_ev = conservative_probability * odds
```

P2初期ではEVを表示するだけとし、自動購入に接続しない。

---

# 15. 見送り判定

見送りは正常な結果であり、エラーではない。

## 15.1 見送り理由コード

```text
DQ_LOW_DATA_QUALITY
DQ_MISSING_REQUIRED_DATA
DQ_POINT_IN_TIME_VIOLATION
MD_MODEL_DISAGREEMENT
MD_LOW_CALIBRATION_CONFIDENCE
MD_OUT_OF_DISTRIBUTION
OD_ODDS_MISSING
OD_ODDS_STALE
OD_ODDS_SHARP_CHANGE
RC_ENTRY_CHANGE
RC_EXHIBITION_UNSTABLE
RC_WEATHER_EXTREME
RM_DAILY_LIMIT_REACHED
RM_MONTHLY_LIMIT_REACHED
```

## 15.2 初期判定

P2では以下を採用する。

- data_quality_score < 90 → 見送り
- 必須オッズ欠落 → 見送り
- OODスコア閾値超過 → 見送り
- モデル間最大差が設定値超過 → 見送り
- 保守期待値が閾値未満 → 見送り

閾値は設定ファイル化し、ハードコードしない。

---

# 16. 仮想購入

## 16.1 初期ルール

- 1購入100円固定
- 1レース最大1点
- 1日上限1,200円
- 実購入機能なし
- 損失後の増額なし
- ケリー基準なし

## 16.2 記録

全候補について、購入したものだけでなく見送ったものも保存する。

```text
race_id
prediction_id
decision_at
combination
odds_at_decision
model_probability
conservative_probability
expected_value
recommended
skip_reason
stake_yen
result
payout_yen
```

## 16.3 返還

返還レースは通常の的中・不的中と分離し、投資額を適切に戻す。

---

# 17. API

## 17.1 ヘルスチェック

```http
GET /health
GET /ready
```

## 17.2 データ取込

```http
POST /api/v1/ingestions
GET  /api/v1/ingestions/{id}
GET  /api/v1/ingestions/{id}/errors
```

## 17.3 レース

```http
GET /api/v1/races?date=YYYY-MM-DD
GET /api/v1/races/{race_id}
GET /api/v1/races/{race_id}/data-quality
```

## 17.4 モデル

```http
POST /api/v1/models/train
GET  /api/v1/models
GET  /api/v1/models/{model_version_id}
POST /api/v1/models/{model_version_id}/activate
```

モデル有効化は管理者操作とする。

## 17.5 予測

```http
POST /api/v1/races/{race_id}/predictions
GET  /api/v1/races/{race_id}/predictions/latest
```

レスポンス例:

```json
{
  "race_id": "uuid",
  "prediction_at": "2026-07-29T14:40:00+09:00",
  "model_version": "win-v1.3.0",
  "probabilities": {
    "1": 0.462,
    "2": 0.196,
    "3": 0.133,
    "4": 0.093,
    "5": 0.068,
    "6": 0.048
  },
  "probability_sum": 1.0,
  "data_quality_score": 97,
  "confidence_score": 81,
  "status": "predicted",
  "warnings": []
}
```

## 17.6 バックテスト

```http
POST /api/v1/backtests
GET  /api/v1/backtests/{id}
GET  /api/v1/backtests/{id}/metrics
GET  /api/v1/backtests/{id}/export.csv
```

---

# 18. 管理画面

## 18.1 ダッシュボード

表示項目:

- 取込最終成功日時
- 取込エラー件数
- データ品質平均
- 予測対象レース数
- 見送り数
- 有効モデル
- 直近30日Log Loss
- 直近30日Calibration Error
- 仮想購入回収率
- 最大ドローダウン

## 18.2 レース詳細

- 出走6艇
- 使用可能データ時刻
- 特徴量一覧
- 各艇1着確率
- モデル別確率
- 校正前後の確率
- 品質警告
- 見送り理由
- オッズ履歴（P2）

## 18.3 モデル詳細

- 学習期間
- 校正期間
- 検証期間
- テスト期間
- ハイパーパラメータ
- 指標
- 場別指標
- 月別指標
- Feature importance
- モデルカード
- Git SHA

---

# 19. CLI

```bash
python -m app.cli.ingest --source official --file data/raw/file.lzh
python -m app.cli.validate --date 2026-07-01
python -m app.cli.snapshot --cutoff 2026-07-01T00:00:00+09:00
python -m app.cli.features build --feature-set v1
python -m app.cli.train --config configs/model_v1.yaml
python -m app.cli.backtest --config configs/backtest_v1.yaml
python -m app.cli.predict --date 2026-07-29
python -m app.cli.export-quality --output data/exports/quality.csv
```

全CLIは終了コードを明確にする。

- 0: 成功
- 1: 入力不正
- 2: データ品質不合格
- 3: DB障害
- 4: モデル障害
- 5: 未分類エラー

---

# 20. 設定ファイル

`configs/`を作成する。

```yaml
project:
  timezone: Asia/Tokyo
  random_seed: 20260729

quality:
  prediction_min_score: 90

training:
  algorithm: lightgbm
  target: winner_lane
  calibration: sigmoid

backtest:
  train_months: 12
  test_months: 1
  min_windows: 12

virtual_bet:
  stake_yen: 100
  max_daily_stake_yen: 1200
  enabled: true

real_bet:
  enabled: false
```

`real_bet.enabled`は常にfalseとし、コード上も実装しない。

---

# 21. テスト戦略

## 21.1 Unit Test

- パーサー
- ID生成
- 日時変換
- 市場確率正規化
- 確率合計
- 品質スコア
- 見送り判定
- 仮想払戻

## 21.2 Integration Test

- ファイル→DB
- DB→特徴量
- 特徴量→予測
- 予測→バックテスト
- Migration up/down

## 21.3 Contract Test

- APIレスポンス
- CSV列
- モデルartifact metadata

## 21.4 Leakage Test

必須テスト:

1. prediction_at後の統計値を登録
2. 特徴量生成
3. 未来値が使われないことを確認

## 21.5 Reproducibility Test

同じsnapshot・seed・commitで学習し、指標と予測が許容差内で一致すること。

## 21.6 Property Test

- 確率は0〜1
- 合計は1
- 市場確率合計は1
- 払戻額は負数にならない
- 1レース6艇を超えない

---

# 22. セキュリティ

- 管理画面は認証必須
- 初期は単一管理者
- パスワードはArgon2
- CSRF対策
- SQLインジェクション対策
- アップロード拡張子・MIME・サイズ検査
- 圧縮爆弾対策
- ファイル名を信用しない
- SecretsをGitへ保存しない
- 監査ログを保存
- 外部URL取得先をallowlist化

---

# 23. ログ・監査

## 23.1 構造化ログ

JSON形式。

```json
{
  "timestamp": "2026-07-29T09:00:00+09:00",
  "level": "INFO",
  "event": "prediction.created",
  "race_id": "uuid",
  "model_version_id": "uuid",
  "dataset_snapshot_id": "uuid",
  "correlation_id": "uuid"
}
```

## 23.2 監査対象

- データ取込
- データ削除
- モデル学習
- モデル有効化
- 予測実行
- 設定変更
- 仮想購入ルール変更
- CSV出力

---

# 24. バックアップ

## 24.1 対象

- PostgreSQL
- raw source files
- dataset snapshots
- model artifacts
- config
- audit logs

## 24.2 方針

- 日次DBバックアップ
- 週次フルバックアップ
- SHA-256検証
- 月1回リストア試験
- バックアップ成功だけでなく復元成功を記録

---

# 25. フェーズ別実装タスク

# P0 データ監査基盤

## P0-T001 プロジェクト初期化

成果物:

- Docker Compose
- FastAPI起動
- PostgreSQL接続
- Alembic
- pytest
- README

受入:

- `docker compose up`で起動
- `/health`が200
- migration成功
- test成功

## P0-T002 データソース台帳

- data_sources
- source_files
- ingestion_runs
- ingestion_errors

## P0-T003 レース基礎スキーマ

- venues
- meetings
- races
- racers
- entries
- results

## P0-T004 公式ファイル取込

- 圧縮解凍
- 文字コード判定
- parser versioning
- hash
- 冪等取込

## P0-T005 時点管理

- available_at
- published_at
- valid_from/to
- leakage test

## P0-T006 品質検査

- ルール実装
- 品質スコア
- rejected records
- レポート

## P0-T007 データスナップショット

- manifest
- hash
- row counts
- cutoff_at

## P0-T008 管理画面

- 取込履歴
- エラー
- 品質
- レース一覧

### P0完了条件

- 3か月以上の過去データを取込可能
- 同一ファイル再取込で重複なし
- 過去1レースを時点再現可能
- 未来情報混入テスト成功
- 品質不合格レースを停止可能

P0完了まではP1へ進まない。

---

# P1 1着確率モデル

## P1-T001 Feature Set V1

- SQL/Python特徴量生成
- バージョン保存
- 時点制約

## P1-T002 Baseline A/B

- 枠番率
- 多項ロジスティック回帰

## P1-T003 LightGBM

- multiclass
- seed固定
- model artifact保存

## P1-T004 CatBoost

- 同一splitで比較

## P1-T005 時系列検証

- rolling windows
- month/venue metrics

## P1-T006 確率校正

- sigmoid/isotonic比較
- calibration set分離

## P1-T007 モデルカード

- 用途
- 非用途
- データ期間
- 指標
- 制約
- 既知の弱点

## P1-T008 予測API・画面

### P1完了条件

- 確率合計1
- Baseline比較済み
- 未使用Test期間を保持
- 校正前後を比較可能
- 同一snapshotで再現可能
- 利益ではなく予測品質で採否判断

---

# P2 市場比較・見送り・仮想購入

## P2-T001 オッズスナップショット

## P2-T002 市場確率正規化

## P2-T003 保守確率

## P2-T004 見送りルール

## P2-T005 固定100円仮想購入

## P2-T006 収支レポート

## P2-T007 オッズ時点バックテスト

### P2完了条件

- オッズ時刻保存
- 締切後データ混入なし
- 見送り理由表示
- 仮想購入のみ
- 月別・場別・オッズ帯別評価
- 6か月以上のフォワードテスト計画を出力

---

# P3 進入予測・2連単

## P3-T001 進入データモデル

## P3-T002 進入確率モデル

## P3-T003 1着条件付き2着モデル

## P3-T004 2連単確率

## P3-T005 組み合わせ確率校正

## P3-T006 仮想運用

P3はP2のフォワードテストが安定してから着手する。

---

# 26. Claude Codeの作業単位

Claude Codeは、1タスクにつき以下を必ず提出する。

```text
1. 実装概要
2. 変更ファイル一覧
3. DB変更
4. API変更
5. テスト追加
6. 実行コマンド
7. テスト結果
8. 既知の制約
9. 次タスクへの引継ぎ
```

1タスクで大規模な横断変更を行わない。

1タスクの目安:

- 変更ファイル15個以内
- migration 1〜2個
- 明確な受入条件1セット

---

# 27. Claude Codeへ最初に渡す実装プロンプト

```text
このリポジトリに、添付の「Claude Code向け設計実用書」を基準として
P0-T001「プロジェクト初期化」だけを実装してください。

重要:
- 次タスクへ進まないでください。
- 自動購入機能を実装しないでください。
- Python 3.12、FastAPI、PostgreSQL、SQLAlchemy 2、Alembic、pytest、Docker Composeを使用してください。
- タイムゾーンはAsia/Tokyoです。
- .env.exampleを作成し、秘密情報は含めないでください。
- health endpoint、DB接続テスト、migrationテストを含めてください。
- READMEに起動・停止・テスト・migration手順を記載してください。
- 完了時に、変更ファイル一覧、実行コマンド、テスト結果、残課題を報告してください。
```

---

# 28. コードレビュー基準

## 必須

- 型ヒント
- Docstringは複雑な公開関数のみ
- 例外を握りつぶさない
- `except Exception: pass`禁止
- SQL直書き時はパラメータ化
- UTC保存・Asia/Tokyo表示
- 金額は整数円
- 確率はfloat、表示時のみ丸める
- seed固定
- configをコードへ直書きしない
- テストなしの業務ロジック追加禁止

## 却下条件

- 未来情報混入の可能性
- データソース不明
- モデルartifact未管理
- migrationなしのスキーマ変更
- notebookに本番処理
- 自動購入への接続
- 大量スクレイピング
- 回収率だけを根拠にモデル採用

---

# 29. 運用手順

## 日次

1. データ取込
2. hash検証
3. 品質検査
4. 予測対象抽出
5. 予測
6. 見送り判定
7. 結果確定後に照合
8. 仮想収支更新

## 週次

- 欠損率
- パーサーエラー
- 場別性能
- 校正誤差
- ドリフト
- 見送り率

## 月次

- モデル再学習候補評価
- 現行モデルとの比較
- 未使用検証期間で評価
- 管理者承認後のみ切替

モデルを自動で本番昇格させない。

---

# 30. 最終受入基準

## P0

- データ由来を追跡できる
- 過去時点を再現できる
- 未来情報を排除できる
- 冪等取込
- 品質停止

## P1

- 確率モデルが再現可能
- 確率合計100%
- 校正評価あり
- Baseline比較あり
- 時系列検証あり

## P2

- オッズ時刻を再現可能
- 市場確率を正規化
- 見送り理由を説明
- 固定100円仮想購入
- 実購入なし

## 全体

- 監査ログ
- バックアップ
- テスト
- ドキュメント
- セキュリティ基本要件
- 利益保証表現なし

---

# 31. 参考資料

本設計は、公式に提供されている全国24場の番組表・競走成績ダウンロードを主要な履歴データ候補としている。BOAT RACE公式サイトでは番組表・競走成績のダウンロードが案内されている一方、旧来の一部閲覧サービスは2025年3月5日に終了しているため、取得処理は現行提供方式に合わせて実装すること。

また、公式サイトポリシーは不正アクセス、大量の情報送受信、大量アクセスなどサイト運営へ支障を与える行為を禁止している。したがって、本システムは大量巡回や非公開API依存を前提にしない。

確率モデルは校正を必須工程とし、時系列データの検証では未来データを学習側へ混ぜない分割を採用する。

参照URLは実装時に`docs/source-register.md`へ登録し、確認日を記録すること。

- BOAT RACE公式「ダウンロード・他」
- BOAT RACE公式「サイトポリシー」
- BOAT RACE公式「データを調べる」
- scikit-learn Probability Calibration
- scikit-learn TimeSeriesSplit

---

# 32. 最終指示

最初に着手するのはP0-T001のみである。

予想精度を早く確認する目的で、データ監査・時点管理・テストを省略してはならない。

本プロジェクトの品質は、モデルの複雑さではなく、次の4点で評価する。

1. データが正しい
2. 過去時点を再現できる
3. 確率が校正されている
4. 判断と結果を監査できる

以上。
