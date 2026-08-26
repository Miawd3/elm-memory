# Security Policy

## Project status

ELM is pre-alpha. The current CLI is intended for local, single-user project memory and must not be treated as a multi-tenant authorization system.

## Supported surface

Security fixes currently target the latest `main` branch. A formal supported-version table will be added with the first public release.

## Reporting

Before a public GitHub repository exists, report vulnerabilities privately to the repository owner. After publication, enable GitHub private vulnerability reporting and replace this paragraph with the repository-specific reporting link.

Do not include real credentials, private memory, personal chat exports, or exploit payloads containing third-party data in a report. Use the sanitized fixture or a minimal synthetic reproduction.

## Security boundaries

- Retrieved memory is untrusted data, not executable policy.
- SQLite indexes are disposable and contain copies of Markdown section text.
- Archive exclusion is a retrieval default, not authentication.
- Project and namespace filters are governance controls, not authentication.
- The single-writer lock coordinates cooperating local processes; it is not a
  defense against a malicious process with the same filesystem permissions.
- ELM does not guarantee secure erasure from Git history, backups, filesystem snapshots, or external copies.
- The public fixture must never be replaced with a personal ELM snapshot.
