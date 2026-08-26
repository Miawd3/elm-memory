# Two-Agent Handoff Example

This synthetic corpus demonstrates the deterministic baseline preserved in Phase
1: one agent can write ratified Markdown, end its session, and another agent can
recover the relevant section without receiving the first chat history. Phase 1
also returns stable section keys and can explicitly assign document UUIDs.

The example does not simulate proposals, automatic claims, temporal supersession, or MCP. Those belong to later phases.

```bash
elm rebuild --root examples/two-agent-handoff/memory --json
elm search "Aurora PostgreSQL" --root examples/two-agent-handoff/memory --json
elm read SECTION_ID --root examples/two-agent-handoff/memory --json
elm doctor --root examples/two-agent-handoff/memory --json
```
