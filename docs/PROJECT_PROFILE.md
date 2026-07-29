# Project profile: statistical public-racing prediction system

## Objective

Build a reproducible analytical system that estimates race probabilities, compares them with market information, recommends abstention when uncertainty is high, and evaluates paper performance. It does not promise profit and does not automate betting in the initial scope.

## Default architecture

- Python 3.12+
- FastAPI for service APIs when required
- PostgreSQL as the primary database
- SQLAlchemy/Alembic
- pandas or Polars only when justified by dataset size and team standards
- scikit-learn plus LightGBM/CatBoost after baseline models
- batch-first processing before queues or distributed systems

## Non-negotiable constraints

- P0 data audit and temporal reconstruction precede ML.
- `available_at <= prediction_at` for every feature.
- Random train/test split is prohibited.
- Prediction, probability calibration, market comparison, and action policy remain separate.
- Initial operation is paper simulation with fixed stakes.
- Automated betting, payment, or account operation is out of scope.
- Official/downloadable or licensed data is preferred; terms-sensitive collection requires approval.

## Delivery phases

1. P0: ingestion, identifiers, temporal availability, quality reports, replay.
2. P1: multiclass first-place probability, baselines, walk-forward validation, calibration.
3. P2: timestamped odds, normalized market probability, conservative expected value, abstention, fixed-stake paper simulation.
4. P3: entry-course model, conditional second-place model, exacta probabilities.
5. Three-way combinations only after P0–P3 demonstrate stable value.
