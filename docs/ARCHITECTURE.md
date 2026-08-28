# Architecture

ELM has one central rule: durable memory belongs in readable Markdown, not in a hidden database or provider account.

## System shape

```text
                          canonical state
                    +-----------------------+
                    | Markdown memory root  |
                    | projects, decisions,  |
                    | context, provenance   |
                    +-----------+-----------+
                                |
                         sync / rebuild
                                |
                                v
                    +-----------------------+
                    | SQLite + FTS5 index   |
                    | disposable projection |
                    +-----------+-----------+
                                |
              +-----------------+-----------------+
              |                                   |
              v                                   v
       CLI JSON contract                   local stdio MCP
              |                                   |
              +-----------------+-----------------+
                                |
                                v
                    bounded context for agents
```

Deleting `<root>/.elm/index.sqlite` loses no canonical knowledge. `elm rebuild` recreates it from Markdown and governed JSON records.

## Memory root

A small root created by `elm init` looks like this:

```text
memory/
  00_registry/
    ROOT_INDEX.md
    ELM_ROOT_ID.json
  10_shared/
    SHARED_CONTEXT.md
  20_projects/
    my-project/
      PROJECT_HUB.md
      ACTIVE_CONTEXT.md
      DECISIONS.md
  99_archive/
  backups/
  .elm/
    index.sqlite
```

`ELM_ROOT_ID.json` is an immutable portable identity created by `elm init`. It lets explicitly enabled mutation profiles bind to one root without relying on a machine-specific path.

`99_archive` and directories named `backups` are excluded from ordinary retrieval. Historical access must be explicit.

## Parsing and identity

ELM indexes Markdown by whole sections. Metadata fields such as `Title`, `Scope`, `Tags`, `Related files`, `Last updated`, `Status`, and `Summary` make retrieval and diagnostics more predictable.

Documents may carry an explicit `ELM ID: doc_<uuid>` added by the opt-in `elm ids assign` migration. Sections expose derived stable keys based on that document ID and heading path. Documents without explicit IDs use path-bound keys. Integer database IDs remain compatibility details and are not durable references.

Indexing never inserts IDs or otherwise rewrites Markdown.

## Retrieval

`elm search` uses SQLite FTS5 and returns compact candidate manifests. `elm context` uses the same deterministic retrieval and packs whole sections under a requested estimated-token budget.

A packet contains:

- authority and safety warnings;
- source manifests with stable `elm://section/...` locators;
- selected whole sections quoted as `untrusted_memory_data`;
- the requested and estimated token counts;
- the resolved project, namespace, archive, and status scope.

A section that does not fit is represented by a manifest instead of being silently truncated or model-summarized. `read`, `outline`, and `related` provide progressive expansion.

The token estimator is deterministic and provider-independent. It is a local packing budget, not billed-token accounting.

## Freshness and traces

Direct CLI reads may sync changed Markdown before retrieval. MCP reads never sync: an agent calls `status`, and a stale index fails closed until an operator runs `elm sync` or `elm rebuild`.

By default, `context` writes a disposable trace containing hashes, filters, section keys, timing, and token estimates. It contains no source body and no raw task text. `--no-trace` disables it; `--trace-query-text` is an explicit privacy-sensitive opt-in.

## Concurrency and recovery

Readers can run concurrently. Index writes and canonical mutations use one writer lock. A competing writer waits for a bounded period or exits cleanly.

Governed multi-file transitions use a canonical journal and atomic replacement. If a process is interrupted, `elm recover --dry-run` shows the affected paths before `--apply` performs a bounded rollback. Recovery refuses to overwrite unexpected human edits.

## Governed memory

ELM separates evidence, proposals, accepted claims, and history:

```text
source locator + hash
          |
          v
      proposal  ---- reject / defer
          |
   explicit transition
          |
          v
    accepted claim ---- dispute / supersede / delete
          |
          v
 immutable events and metadata-only tombstones
```

Evidence records store locators and hashes, not raw evidence payloads. Actor strings are provenance labels, not authenticated identities.

Accepted claims are canonical files. Superseded, disputed, future-effective, expired, and deleted records are hidden from normal retrieval but remain available through explicit history where applicable.

## MCP profiles

The stdio adapter delegates every operation to the canonical CLI JSON contract in a separate process. It has no independent database, ranking, or trust policy.

### Read-only default

Seven tools: `status`, `search`, `context`, `read`, `related`, `history`, and `stats`.

### Proposal-only

The seven read tools plus `propose_memory`, `list_memory_proposals`, and `preview_memory_transition`. Proposals are untrusted candidates. This profile cannot accept, reject, supersede, dispute, delete, recover, sync, rebuild, migrate, or change policy.

### Autonomous

The seven read tools plus `remember_memory`. It can create bounded, leased `agent_curated` memory only for server-allowlisted projects. That authority remains below current user instructions, verified repository state, and stronger governed memory.

Exact duplicates are reused. Conflicts and quota overflow are deferred. Optional source-verified compare-and-swap can renew or replace only the current `agent_curated` lineage head when both its canonical hash and configured repository bytes match.

Expiry hides a claim from ordinary reads and frees active quota without deleting its canonical history. Logical compaction is a bounded read-only lineage view; it does not rewrite audit history.

## Trust boundary

ELM assumes a local, single-user filesystem boundary. Anyone who can read the root or its SQLite index can read the indexed memory. Anyone who can modify canonical files can change what later retrieval returns.

Retrieved text is data, even when it looks like a prompt. Agent hosts must keep system/developer instructions, current user instructions, and verified repository truth above memory in their authority order.

Project and namespace filters prevent accidental cross-scope retrieval; they are not authentication between mutually hostile users or processes.

## Strengths and tradeoffs

| Strength | Tradeoff |
| --- | --- |
| Markdown remains readable and portable | Good metadata still requires discipline |
| No hosted or model dependency | No cloud sync or multi-user service is included |
| Deterministic bounded packets | Lexical FTS can miss synonym-only matches |
| Exact provenance and history | Governed workflows add more files and concepts |
| Disposable index | First build and changed-file sync still cost local I/O |
| Agent-independent CLI/MCP | Host configuration differs between agents |

Embeddings, model summarization, a graph database, and a hosted server are intentionally absent from v1. They should be added only when a named evaluation failure justifies their privacy, latency, dependency, and maintenance cost.
