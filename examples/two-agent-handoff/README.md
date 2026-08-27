# Two-agent read-only MCP handoff

This cooperative compatibility demo shows two heterogeneous coding-agent hosts
recovering the same accepted project decision from one sanitized ELM core. It
never reads a personal ELM root and never exposes a mutation tool. The stricter
tool-provenance experiment is documented in the
[heterogeneous agent pilot](../../docs/HETEROGENEOUS_AGENT_PILOT.md).

## Prerequisites

- Python 3.11 or newer;
- `elm-memory[mcp]` installed from this checkout;
- authenticated `agy` and `codex` CLIs on `PATH`;
- temporary Antigravity permissions `mcp(elm_demo/status)`,
  `mcp(elm_demo/context)`, and `mcp(elm_demo/read)` in
  `~/.gemini/antigravity-cli/settings.json`; restore the original settings file
  byte-for-byte immediately after the run.

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
cooperative host contract directs recovery through the read-only MCP boundary;
the stricter pilot additionally audits streamed tool provenance.

The report keeps only host versions, boolean checks, the sanitized source
identity, and bounded error categories. It does not retain model conversations,
provider diagnostics, session identifiers, or raw terminal output.

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
