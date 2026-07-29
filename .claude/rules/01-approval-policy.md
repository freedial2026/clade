# Approval policy

## Proceed without approval

Proceed autonomously when the change is local, reversible, reviewable, and within the accepted task:

- file discovery, reading, grep, repository summaries;
- Python scripts for mechanical processing;
- focused tests, linters, static checks, build verification;
- documentation, comments, internal refactoring with preserved behavior;
- adding tests, fixing clear defects, updating task records;
- creating local files under the project and generating diffs;
- non-production Docker builds or local database setup using disposable data.

## Ask before execution

Ask only for consequential actions:

- production deployment, release, external publication, or public PR submission;
- `git push`, merging protected branches, tags, or releases;
- destructive data changes, production migrations, irreversible schema changes;
- billing, purchases, paid API activation, or resource provisioning;
- credential, authentication, authorization, encryption, or privacy-policy changes;
- use or transmission of personal, confidential, regulated, or production data;
- terms-of-service-sensitive collection, scraping, or private API use;
- changing the primary language, framework, database product, or system boundary;
- model promotion to production or material decision-policy changes;
- automated betting, payment, order, or transaction execution.

## When approval is required

Provide: proposed action, reason, exact impact, dry-run result, rollback, and safer alternative. Continue other safe work.
