# ML and statistical rules

- Random train/test splitting is prohibited for time-dependent prediction.
- Feature availability must satisfy `available_at <= prediction_at`.
- Separate predictive probability, calibration, market comparison, and action policy.
- Compare against simple baselines before complex models.
- Track dataset, features, code, model, calibration, and evaluation versions.
- Evaluate uncertainty, calibration, drift, subgroup stability, and operational failure modes.
- Backtests do not prove future profitability. Production promotion requires independent holdout and paper operation.
- Automated wagering is out of scope unless separately approved through legal, risk, and product review.
