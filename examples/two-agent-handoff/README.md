# Two-agent read-only MCP handoff

This demo proves that two heterogeneous coding-agent hosts can recover the same
accepted project decision from one sanitized ELM core. It never reads a personal
ELM root and never exposes a mutation tool.

## Prerequisites

- Python 3.11 or newer;
- `elm-memory[mcp]` installed from this checkout;
- authenticated `agy` and `codex` CLIs on `PATH`;
- the scoped Antigravity permission `mcp(elm/*)` in
  `~/.gemini/antigravity-cli/settings.json`.

Run:

```text
python examples/two-agent-handoff/run_hosts.py --assert-pass
```

The harness copies the synthetic Orion fixture to a temporary directory,
rebuilds its disposable index, and gives both hosts the same absolute stdio
launch command:

```text
python -m elm_memory.mcp_server --root <temporary-synthetic-root>
```

Antigravity/Gemini and Codex each independently
calls ELM `status`, `context`, and `read`, then returns the accepted ODR-001
storage decision with its stable section identity. The demo passes only when
both recover `PostgreSQL 17` from
`20_projects/orion/DECISIONS.md` and return the same `section_key`.

The report keeps only host versions, boolean checks, the sanitized source
identity, and bounded errors. It does not retain model conversations or raw
terminal output.

Claude Code is an additional supported host. After authenticating it, replace
Antigravity in the same acceptance harness:

```text
python examples/two-agent-handoff/run_hosts.py --hosts claude codex --assert-pass
```

Do not use a global auto-approve flag for this demo. Antigravity needs only the
single MCP allow-rule above; the synthetic fixture and MCP configuration are
created inside a temporary workspace and removed after the run.

This is a compatibility demonstration, not an access-control claim. A local
host that can launch the server can read every item allowed by the configured
ELM root and policy filters; namespaces are governance filters, not
authentication.
