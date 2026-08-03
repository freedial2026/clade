# データ定義書

## 1. 基本方針

画面には、意味の曖昧な内部指標名を直接出しません。

- `conservative_ev`、`保守EV`：画面では使用しない
- `expected_return_per_100_yen`：`100円の期待払戻`として表示
- `data_quality_percent`、`データ充足率`：使用しない
- `data_coverage.obtained / data_coverage.total`：`必要データ 12 / 13項目`として表示
- `data_coverage.missing_labels`：`未取得：部品交換情報`として表示

`expected_return_per_100_yen = 108`は、同条件を長期に繰り返した場合の平均払戻見込みが、元本100円を含めて108円という意味です。1回の利益や的中を保証しません。

---

## 2. ルート配列

| PHP変数 | 型 | 必須 | 説明 |
|---|---|---:|---|
| `$dashboard['site']` | array | 必須 | サイト表示・バージョン情報 |
| `$dashboard['risk']` | array | 必須 | 資金・日次上限・紙上投票設定 |
| `$dashboard['data_catalog']` | array | 必須 | 必要データ項目のマスター |
| `$dashboard['venues']` | array[] | 必須 | 当日開催会場 |
| `$dashboard['races']` | array[] | 必須 | 当日残存レース |

---

## 3. `site`

| キー | 型 | 必須 | 例 | 画面・用途 |
|---|---|---:|---|---|
| `page_title` | string | 必須 | `Race Decision Desk` | `<title>` |
| `meta_description` | string | 必須 | 説明文 | description |
| `brand_name` | string | 必須 | `Race Decision Desk` | ヘッダー |
| `brand_subtitle` | string | 必須 | `Multi-venue...` | ヘッダー補足 |
| `operating_mode_label` | string | 必須 | `紙上投票モード` | 運用状態 |
| `model_version` | string | 必須 | `model-v1` | 監査ログ |
| `policy_version` | string | 必須 | `paper-only-v2` | 判断ルール版 |
| `last_updated_at` | ISO 8601 string | 必須 | `2026-08-01T22:04:00+09:00` | 最終更新 |

---

## 4. `risk`

| キー | 型 | 必須 | 単位 | 説明・検証 |
|---|---|---:|---|---|
| `bankroll_yen` | int | 必須 | 円 | 検証用資金。0以上 |
| `spent_today_yen` | int | 必須 | 円 | 本日使用額。セッション・DB値で上書き可 |
| `daily_limit_yen` | int | 必須 | 円 | 日次上限 |
| `minimum_stake_yen` | int | 必須 | 円 | 最小金額。通常100 |
| `stake_unit_yen` | int | 必須 | 円 | 金額単位。通常100 |
| `actual_betting_enabled` | bool | 必須 | — | 本テンプレートでは必ずfalse |
| `paper_betting_enabled` | bool | 必須 | — | 紙上投票の可否 |

---

## 5. `data_catalog`

キーはデータ項目コードです。

```php
'current_odds' => [
    'label' => '現在オッズ',
    'critical' => true,
]
```

| キー | 型 | 必須 | 説明 |
|---|---|---:|---|
| `<data_code>.label` | string | 必須 | 画面へ表示する不足項目名 |
| `<data_code>.critical` | bool | 必須 | 未取得時に判断を停止するか |

`data_availability`のキーは、必ず`data_catalog`のキーと一致させます。

---

## 6. `venues[]`

| キー | 型 | 必須 | 例 | 取得元・計算 |
|---|---|---:|---|---|
| `venue_code` | string | 必須 | `05` | 公式場コード。先頭0保持 |
| `venue_name` | string | 必須 | `多摩川` | 会場マスター |
| `water_type_label` | string | 必須 | `淡水` | 会場マスター |
| `remaining_race_count` | int | 必須 | `3` | 締切前レース数 |
| `candidate_count` | int | 必須 | `1` | `decision_status=candidate`件数 |
| `waiting_count` | int | 必須 | `0` | `decision_status=waiting`件数 |
| `next_race_id` | string | 必須 | `20260801-05-10` | 次締切レースID |
| `required_data_obtained` | int | 算出 | `38` | `prepare_dashboard_data()`が算出 |
| `required_data_total` | int | 算出 | `39` | 同上 |
| `next_race_number` | int|null | 算出 | `10` | 同上 |
| `next_deadline_at` | string|null | 算出 | ISO 8601 | 同上 |

---

## 7. `races[]`

### 7.1 識別・締切

| キー | 型 | 必須 | 例 | 説明 |
|---|---|---:|---|---|
| `race_id` | string | 必須 | `20260801-05-10` | 日付-場-レースの一意ID |
| `venue_code` | string | 必須 | `05` | 先頭0保持 |
| `venue_name` | string | 必須 | `多摩川` | 表示用 |
| `race_number` | int | 必須 | `10` | 1～12 |
| `scheduled_deadline_at` | ISO 8601 string | 必須 | `...+09:00` | B-file由来の締切予定 |

### 7.2 判断状態

| キー | 型 | 必須 | 値 | 説明 |
|---|---|---:|---|---|
| `decision_status` | enum string | 必須 | `candidate` / `waiting` / `skip` | 内部状態 |
| `decision_label` | string | 必須 | `検証候補` | 画面表示。実投票推奨とは表現しない |
| `decision_reasons` | array[] | 必須 | — | 理由一覧 |
| `decision_reasons[].tone` | enum | 必須 | `positive` / `neutral` / `risk` | 表示色 |
| `decision_reasons[].title` | string | 必須 | `展示タイムが上位` | 短い結論 |
| `decision_reasons[].detail` | string | 必須 | `レース内1位` | 根拠 |

### 7.3 買い目と金額

| キー | 型 | 必須 | 例 | 説明 |
|---|---|---:|---|---|
| `recommended_bet.bet_type_code` | string | 必須 | `exacta` | システムコード |
| `recommended_bet.bet_type_label` | string | 必須 | `2連単` | 表示名 |
| `recommended_bet.combination` | string | 必須 | `1→3` | 表示形式 |
| `available_bet_options` | array[] | 必須 | — | 選択可能な候補。見送りなら空配列 |
| `max_stake_yen` | int | 必須 | `400` | 当該レースの紙上投票上限 |

### 7.4 オッズ・期待払戻

| キー | 型 | 必須 | 例 | 表示・注意 |
|---|---|---:|---|---|
| `current_odds` | float|null | 必須 | `5.8` | `5.8倍`。取得時刻を別途DBで保持 |
| `odds_5_minutes_ago` | float|null | 必須 | `5.4` | `5.4倍 → 5.8倍`表示 |
| `odds_history` | float[] | 必須 | `[5.1,...]` | -20/-10/-5/-2/現在 |
| `expected_return_per_100_yen` | int|null | 必須 | `108` | `100円の期待払戻 108円`。元本込み |
| `model_probability` | float|null | 推奨 | `0.201` | 内部診断。TOPには原則表示しない |
| `uncertainty_adjusted_probability` | float|null | 推奨 | `0.186` | 期待払戻計算用。TOPには表示しない |

推奨計算例：

```php
$expectedReturnPer100Yen = (int) floor(
    100 * $uncertaintyAdjustedProbability * $currentOdds
);
```

実装時は、計算式・丸め・モデルバージョンを固定し、DBへ入力値も保存してください。

### 7.5 必要データ

| キー | 型 | 必須 | 説明 |
|---|---|---:|---|
| `data_availability` | array<string,bool> | 必須 | `data_catalog`全項目の取得状態 |
| `data_coverage.obtained` | int | 算出 | 取得済み件数 |
| `data_coverage.total` | int | 算出 | 必要件数 |
| `data_coverage.missing_codes` | string[] | 算出 | 未取得コード |
| `data_coverage.missing_labels` | string[] | 算出 | 未取得表示名 |
| `data_coverage.critical_missing_codes` | string[] | 算出 | 重要未取得コード |
| `data_coverage.state_label` | string | 算出 | 全取得・ほぼ揃う・判断材料不足 |

表示例：

```text
必要データ 12 / 13項目
ほぼ揃っています
未取得：部品交換情報
```

重要項目が不足する場合：

```text
必要データ 10 / 13項目
判断材料が不足しています
未取得：展示進入・現在オッズ・直前情報反映後の予測
投票判断：見送り
```

---

## 8. 欠損時の処理

| 欠損 | 画面 | 紙上投票API |
|---|---|---|
| `current_odds` | `—`、比較不可 | 拒否 |
| `scheduled_deadline_at` | 表示不可 | データ生成段階でエラー |
| critical項目 | 判断材料不足 | 拒否 |
| 非critical項目 | 不足名を表示 | 他条件を満たせば記録可 |
| `expected_return_per_100_yen` | 算出不可 | 原則waiting/skip |
| `available_bet_options=[]` | 買い目なし | 拒否 |

---

## 9. 保存すべき監査データ

紙上投票時に最低限保存します。

- `paper_bet_id`
- `race_id`
- `bet_type_code`
- `combination`
- `stake_yen`
- `odds_at_record`
- `recorded_at`
- `model_version`
- `policy_version`
- 本番では`prediction_id`
- 本番では`odds_snapshot_id`
- 本番では`before_info_snapshot_id`

判断結果だけでなく、判断時点の入力を参照できるIDを保存してください。
