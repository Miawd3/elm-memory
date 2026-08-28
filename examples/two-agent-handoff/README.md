# Two-agent read-only MCP handoff

This cooperative compatibility demo shows two heterogeneous coding-agent hosts
recovering the same accepted project decision from one sanitized ELM core. It
never reads a personal ELM root and never exposes a mutation tool. Evidence and
claim limits are summarized in [Evaluation](../../docs/EVALUATION.md).

## Prerequisites

- Python 3.11 or newer;
- `elm-memory[mcp]` installed from this checkout;
- authenticated `agy` and `codex` CLIs on `PATH`;
- scoped permission for the three read-only demo tools: `status`, `context`, and `read`.

Run:

```text
python examples/two-agent-handoff/run_hosts.py --assert-pass
```

The harness copies the synthetic Orion fixture to a temporary directory,
rebuilds its disposable index, gives every host a separate empty workspace, and
gives both hosts the same absolute stdio launch command:

```text
python -m elm_memory.mcp_server --root <temporary-synthetic-root>
```

Antigravity/Gemini and Codex each independently
calls ELM `status`, `context`, and `read`, then returns the accepted ODR-001
storage decision with its stable section identity. The demo passes only when
both recover `PostgreSQL 17` from
`20_projects/orion/DECISIONS.md` and return the same `section_key`.

Expected answer and source values are evaluator-side only. The response schema
does not contain them, and the agent workspaces do not contain the fixture. The
cooperative host contract directs recovery through the read-only MCP boundary.

The report keeps only host versions, boolean checks, the sanitized source
identity, and bounded error categories. It does not retain model conversations,
provider diagnostics, session identifiers, or raw terminal output.

Claude Code is an additional supported host. After authenticating it, replace
Antigravity in the same acceptance harness:

```text
python examples/two-agent-handoff/run_hosts.py --hosts claude codex --assert-pass
```

Do not use a global auto-approve flag for this demo. The synthetic fixture and
MCP configuration are created inside a temporary workspace and removed after
the run.

This is a compatibility demonstration, not an access-control claim. A local
host that can launch the server can read every item allowed by the configured
ELM root and policy filters; namespaces are governance filters, not
authentication.
