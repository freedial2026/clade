# MySQL alternative profile

Use only when existing MySQL operations, backup, monitoring, and team expertise outweigh PostgreSQL-specific analytical and temporal conveniences.

Before changing:

- create an ADR;
- verify timestamp precision/timezone behavior;
- map JSONB/JSON differences;
- map constraints, partial indexes, generated columns, UPSERT, and SQL functions;
- update migration and test tooling;
- prove point-in-time queries and data-quality checks remain correct.
