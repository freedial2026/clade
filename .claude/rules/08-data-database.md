# Data and database rules

- PostgreSQL is the default for this profile; changing the database product is an architectural decision requiring approval.
- Every migration needs forward behavior, rollback or compensating action, data-volume estimate, lock-risk analysis, and verification SQL.
- Production migrations require backup confirmation and explicit approval.
- Preserve event time, publication time, collection time, and availability time for temporal systems.
- Never update historical records with knowledge that was unavailable at the historical decision time.
- Store raw source data separately from normalized and derived data.
