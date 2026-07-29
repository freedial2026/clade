# Testing rules

- Add or update tests for behavior changes.
- Prefer deterministic, isolated tests and fixed seeds where randomness is unavoidable.
- Run the narrowest relevant test first.
- Record exact commands and outcomes.
- A skipped test is not a passed test; document why it is skipped.
- Database changes require migration tests, rollback analysis, and representative fixtures.
- ML changes require temporal validation, leakage checks, calibration checks, and reproducibility metadata.
