# GitHub Actions Hardening — `.github/workflows/ci.yml`

| Severity | Count |
|---|---:|
| 🔴 CRITICAL | 0 |
| 🟠 HIGH | 0 |
| 🟡 MEDIUM | 0 |
| 🔵 LOW | 0 |
| ⚪ INFO | 0 |

No issues found. Checked: triggers, injection sinks, permissions, action pinning, secret handling, credential persistence, and runner trust.

## Verified controls

- Uses `pull_request`, `push`, and manual dispatch; no privileged `pull_request_target`, `workflow_run`, `issue_comment`, or `issues` trigger.
- Declares `permissions: contents: read` and grants no write scope.
- Contains no secrets or cloud credentials.
- Uses GitHub-hosted runners rather than persistent self-hosted runners.
- Pins `actions/checkout` and `actions/setup-python` to full 40-character commit SHAs.
- Sets `persist-credentials: false` on every checkout.
- Contains no attacker-controlled `${{ github.event.* }}` interpolation in shell commands.
- Uses only static matrix expressions in runner and setup fields.
- Includes Dependabot configuration for reviewable GitHub Actions updates.

> Review each change before committing. Nothing has been modified.
