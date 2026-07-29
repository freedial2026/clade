# Security and privacy

- Treat `.env`, credentials, private keys, tokens, production dumps, and personal data as sensitive.
- Do not print secrets to logs or include them in exceptions.
- Use least privilege and deny-by-default at production boundaries.
- Validate external input; parameterize database operations; constrain file paths.
- Review dependencies and provenance before adding them.
- Security-sensitive changes require threat analysis and explicit approval before deployment.
- A hook is not a security boundary. Enforce controls through OS, IAM, protected branches, database permissions, and deployment policy.
