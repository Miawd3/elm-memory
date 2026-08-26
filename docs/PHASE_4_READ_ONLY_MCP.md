# Phase 4 — Read-only MCP adapter

Status: implemented

Date: 2026-08-26

## Objective

Phase 4 makes the verified ELM read contract available to heterogeneous coding
agents without creating a second memory implementation. Markdown remains the
canonical store, SQLite remains disposable derived state, and the CLI remains
the only interface that may refresh the index or mutate canonical memory.

```text
agent host
  -> local MCP stdio session
  -> ELM read-only adapter bound to one absolute root
  -> isolated `elm ... --json --no-sync` subprocess
  -> existing CLI policy, retrieval, and SQLite read path
  -> canonical Markdown identity and source locators
```

The adapter uses the official MCP Python SDK v2 and is an optional install:

```text
python -m pip install -e ".[mcp]"
elm-mcp --root /absolute/path/to/memory
```

An explicit root is required. MCP does not use host prompt text to select or
change that root.

## Exposed tools

The server exposes exactly these tools:

| Tool | Purpose | MCP-specific write behavior |
| --- | --- | --- |
| `status` | Check index existence, schema, integrity, freshness, and pending transactions | None |
| `search` | Return compact matching section manifests | Uses `--no-sync` |
| `context` | Compile a bounded source-linked packet | Uses `--no-sync --no-trace` |
| `read` | Read one exact stable section key or compatibility numeric ID | Opens the index read-only |
| `related` | Follow explicit incoming and outgoing document links | Uses `--no-sync` |
| `history` | Query governed claim history | Uses `--no-sync` |
| `stats` | Return index and governance counts | Uses `--no-sync` |

There are no proposal, ratification, deletion, recovery, migration, sync, or
arbitrary-file tools. All seven tools carry the MCP `readOnlyHint`; this is a
client hint and is not treated as an authorization mechanism.

## Read-only and freshness contract

MCP reads open the existing database through SQLite URI `mode=ro` and set
`PRAGMA query_only=ON`. They do not create the `.elm` directory, create a
database, migrate a schema, synchronize changed Markdown, or write a retrieval
trace.

`status` is the readiness gate. It checks:

- that the derived index exists;
- that its schema version exactly matches the client;
- `PRAGMA quick_check`;
- every active Markdown path against indexed size, mtime, and SHA-256 content;
- removed index entries;
- pending canonical governance transactions.

If `healthy` is false or `sync_required` is true, the agent must not claim the
index is current. An operator can run `elm sync --json` or `elm rebuild --json`
outside MCP and call `status` again.

## CLI equivalence and failure isolation

The adapter launches the installed package's `elm_memory.cli` module with a
fixed root and decodes its JSON response. It does not reimplement query parsing,
ranking, context packing, stable identity, archive classification, namespaces,
claim history, or link traversal.

Integration tests compare CLI and MCP section identities and scope behavior.
The server converts a failed or timed-out CLI subprocess into a bounded MCP tool
error. A broken MCP session cannot hold the CLI writer lock or prevent a later
direct CLI invocation.

## Authority and security boundary

Retrieved Markdown is untrusted data. Stored text that resembles a prompt does
not become an instruction. Current user instructions and verified repository
state continue to outrank ELM content.

Project, namespace, history, and archive parameters are retrieval/governance
filters. They prevent accidental broad reads through the ELM contract, but they
are not authentication between mutually untrusted local users. A client able to
launch the server under the same OS account can read every item permitted by the
bound root and policy parameters.

For actual private isolation, use OS/filesystem boundaries or a future
authenticated service. Phase 4 does not claim multi-tenant security.

## Host configuration

Every host launches the same stdio command:

```text
python -m elm_memory.mcp_server --root /absolute/path/to/memory
```

Codex, Claude Code, and Antigravity use different configuration syntax, but no
host receives unique ELM logic. The reproducible configurations live in
`examples/two-agent-handoff/run_hosts.py` and are created only in a temporary
synthetic workspace.

Antigravity headless mode requires one scoped permission:

```json
{
  "permissions": {
    "allow": ["mcp(elm/*)"]
  }
}
```

Merge that entry into the existing
`~/.gemini/antigravity-cli/settings.json`; do not replace unrelated settings.
The host configuration itself is workspace-local at `.agents/mcp_config.json`.

## Validation surfaces

- Unit/integration tests enumerate the exact tool set and read-only annotations.
- CLI/MCP parity tests compare stable identities and policy-filtered results.
- `benchmarks/run_mcp_demo.py` exercises all seven tools in memory through the
  official SDK client and verifies failure isolation and absence of traces.
- `examples/two-agent-handoff/run_hosts.py` gives a copied synthetic Orion root
  to two independent model hosts and requires the same accepted decision and
  `section_key` from both.
- CI runs the complete suite and MCP benchmark on Windows and Linux.

## Explicitly deferred

- MCP mutation tools;
- actor authentication or multi-tenant authorization;
- raw evidence payload storage;
- HTTP deployment and remote credentials;
- embeddings, learned reranking, or model-generated summaries;
- automatic sync from a read call.

Controlled mutation is a separate Phase 5 design problem. Phase 4 does not
grant an agent authority to accept a proposal merely because it can read ELM.

## Protocol references

- MCP specification: <https://modelcontextprotocol.io/specification/2025-11-25>
- Official MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- Codex MCP documentation: <https://developers.openai.com/codex/mcp/>
- Claude Code MCP documentation: <https://code.claude.com/docs/en/mcp>
- Antigravity MCP documentation: <https://www.antigravity.google/docs/cli/mcp/>
