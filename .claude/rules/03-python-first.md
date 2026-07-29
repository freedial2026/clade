# Python-first mechanical operations

Use Python for deterministic, repeatable operations including:

- bulk rename, replacement, formatting, extraction, validation, and report generation;
- CSV/JSON/YAML transformations and schema checks;
- repository inventory, log reduction, duplicate detection, and checksums;
- test fixture creation and migration dry-runs;
- comparing expected and actual structures.

Requirements:

- scripts must be idempotent or support `--dry-run`;
- validate paths and inputs;
- fail clearly with non-zero exit codes;
- avoid network calls unless explicitly required;
- include unit tests for reusable or high-impact scripts;
- do not use an LLM to repeat operations that a short script can perform exactly.
