# BOAT RACE 複数会場ダッシュボード PHPテンプレート v4

## 目的

PHP 8.2以上で動作する、複数会場対応の紙上投票ダッシュボードです。
画面へ入るデータは `data/sample-dashboard.php` に集約し、`index.php` では明示的な変数名で表示しています。

## 構成

```text
boatrace_php_multi_venue_dashboard_v4/
├── index.php                    # PHP画面テンプレート
├── bootstrap.php                # データ読込・セッション初期化
├── assets/
│   ├── styles.css
│   └── app.js
├── data/
│   └── sample-dashboard.php     # 変数へ入れるデモデータ
├── src/
│   └── helpers.php              # 表示・データ充足計算
├── api/
│   ├── dashboard.php            # JSON確認用
│   └── paper-bet.php            # セッション内の紙上投票記録
├── docs/
│   ├── DATA_DEFINITION.md       # データ定義書
│   ├── SCREEN_VARIABLE_MAP.md   # 画面位置とPHP変数の対応
│   └── data_definition.csv      # データ定義のCSV版
├── preview_standalone.html      # PHP不要の表示確認用
└── README.md
```

## 起動

```bash
php -S 127.0.0.1:8080
```

ブラウザで次を開きます。

```text
http://127.0.0.1:8080/index.php
```

## 本番データへの切替

`data/sample-dashboard.php`と同じ配列構造を返すPHPファイルを作成し、環境変数で指定できます。

```bash
DASHBOARD_DATA_FILE=/path/to/production-dashboard.php php -S 127.0.0.1:8080
```

## 重要な表示方針

- `保守EV`という名称は使用しません。
- `expected_return_per_100_yen`を「100円の期待払戻」として表示します。
- データ状態はパーセントで表示しません。
- `取得済み件数 / 必要件数`と、`missing_labels`を表示します。
- 重要データが不足した場合は、紙上投票APIでも拒否します。
- 実投票、自動購入、追加入金、連敗時増額は実装していません。

## 検証

```bash
find . -name '*.php' -print0 | xargs -0 -n1 php -l
```
