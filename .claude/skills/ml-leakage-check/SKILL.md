---
name: ml-leakage-check
description: Audit an ML pipeline for temporal leakage, invalid validation, and probability/decision coupling.
disable-model-invocation: true
---

# ml-leakage-check


- Trace each feature to source and `available_at`.
- Verify time-aware split and untouched holdout.
- Check target leakage, cross-fold leakage, duplicate entities, calibration, and market/decision data timing.
- Produce blocking findings and required tests.

