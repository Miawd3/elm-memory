# Two-Agent Handoff Example

This synthetic corpus demonstrates the Phase 0 capability that already exists: one agent can write ratified Markdown, end its session, and another agent can recover the relevant section without receiving the first chat history.

The example does not simulate proposals, automatic claims, temporal supersession, or MCP. Those belong to later phases.

```bash
elm rebuild --root examples/two-agent-handoff/memory --json
elm search "Aurora PostgreSQL" --root examples/two-agent-handoff/memory --json
elm read SECTION_ID --root examples/two-agent-handoff/memory --json
elm doctor --root examples/two-agent-handoff/memory --json
```
