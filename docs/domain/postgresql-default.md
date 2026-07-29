# PostgreSQL既定方針

このプロファイルでは、時点管理、複雑な分析SQL、JSONB、制約、ウィンドウ関数、Python連携を重視しPostgreSQLを既定とします。

ただし、予測精度はDB製品で決まりません。既存運用がMySQLへ統一され、分析処理をPythonへ寄せる場合はMySQLも可能です。変更時はADRを作成し、型、JSON、時刻、制約、UPSERT、migration、SQL互換性を評価してください。
