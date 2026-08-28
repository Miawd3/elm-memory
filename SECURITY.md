# Security policy

## Supported versions

| Version | Security fixes |
| --- | --- |
| 1.x | Yes |
| Earlier development versions | No |

## Report a vulnerability

Before publishing the repository, the maintainer must enable GitHub private vulnerability reporting under **Settings → Code security** after changing the repository visibility. GitHub does not expose that reporting endpoint while the repository is private.

Once it is enabled, use [GitHub private vulnerability reporting](https://github.com/Miawd3/elm-memory/security/advisories/new). Do not open a public issue for a vulnerability that could expose private memory or enable unsafe mutation.

Use synthetic data in reports. Never include real credentials, personal chat exports, private memory roots, or third-party data.

## Security boundary

- ELM is a local, single-user tool. It is not a multi-tenant authorization service.
- Retrieved memory is untrusted data, not executable policy.
- The SQLite index contains copies of Markdown and must be protected like the canonical root.
- Archive, project, namespace, history, and sensitivity filters are governance controls, not authentication.
- Actor values are caller-supplied provenance labels, not authenticated identities.
- Autonomous MCP is standing permission for bounded `agent_curated` writes in explicitly allowlisted projects. It cannot claim stronger authority.
- Source-verified compare-and-swap proves that configured local bytes matched a digest at transition time. It does not prove authorship or semantic truth.
- The writer lock coordinates cooperating processes; it does not stop a malicious process with the same filesystem permissions.
- Deletion removes active canonical state and preserves a metadata-only tombstone. It cannot erase Git history, backups, snapshots, shell history, or provider logs.
- Release checksums detect accidental or malicious file changes only when the checksum manifest came from the expected release.

## Release artifacts

Windows v1.0 artifacts are not code-signed. Verify SHA-256 checksums before use. The installer runs without administrator access and never owns or removes user memory roots.

Linux installation scripts operate only inside the configured per-user runtime directory and command-link directory. Review the script before use when local policy requires it.
