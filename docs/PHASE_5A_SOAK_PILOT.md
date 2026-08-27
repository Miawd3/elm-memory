# Phase 5A soak pilot

Status: reproducible offline pilot implemented; Phase 5B remains inactive

Date: 2026-08-26

## Purpose

The Phase 5A soak pilot tests whether several independent logical agents can use
the proposal-only MCP profile without creating duplicate canonical proposals,
orphan records, accepted-memory changes, or an apparently healthy stale index.
It exercises the real MCP adapter, CLI subprocess boundary, writer lock,
canonical JSON records, SQLite projection, and recovery path against disposable
synthetic roots.

The pilot is a post-acceptance reliability test. It does not widen the Phase 5A
authority boundary and does not implement or simulate Phase 5B ratification.

## Reproducible workload

`benchmarks/run_phase5a_soak.py` creates a fresh temporary ELM root for each
scenario. Every request body and submission UUID is derived deterministically
from the scenario and operation ordinal. Canonical proposal IDs, root IDs,
timestamps, scheduling, and latency remain runtime values, so repeated reports
are semantically comparable but are not expected to be byte-for-byte identical.

The default CI profile repeats the full matrix twice. Each repetition uses six
logical agents and two unique operations per agent in the writer-contention
scenario:

```bash
python benchmarks/run_phase5a_soak.py --assert-pass
```

An intentionally heavier local run can be requested without touching a real ELM
root:

```bash
python benchmarks/run_phase5a_soak.py \
  --agents 12 \
  --operations-per-agent 10 \
  --max-retries 10 \
  --repetitions 5 \
  --assert-pass
```

The command rejects more than 16 logical agents, more than 20 operations per
agent, more than 10 transient retries, or more than 20 repetitions. All roots
are created under an operating-system temporary directory and removed when the
run ends.

## Scenario matrix

| Scenario | Expected invariant |
| --- | --- |
| MCP surface boundary | Default remains exactly seven read tools; explicit proposal-only remains exactly ten tools and contains no accepted-state executor. |
| Concurrent exact replay | One canonical proposal is created; every other caller converges to the same idempotent replay. |
| Concurrent conflicting replay | One normalized payload wins for a shared submission ID; the other payload is refused and cannot create a second proposal. |
| Unique writer contention | All independent proposals eventually commit through the single-writer boundary with no orphan evidence or transaction journal. |
| Durable project quota | Only the configured number of pending proposals commits even when several server instances race. |
| Process rate limit | A single server process refuses calls beyond its configured per-minute brake without corrupting canonical state. |
| Stale projection repair | A canonical commit without projection makes governed reads fail closed; an explicit operator sync repairs the projection and the same server resumes. |

Transient stale-projection refusals during contention may be retried with the
same submission ID. The report counts every bounded retry. Permanent conflicts,
quota refusals, rate limits, and unexpected errors are never retried as if they
were transient.

## Pass and failure gates

Every scenario must satisfy all of these applicable gates:

- the expected success/refusal distribution is observed;
- the canonical proposal count matches the workload invariant;
- no evidence orphan or pending transaction journal remains;
- no accepted claim or governance transition event is created;
- the governance projection is current and healthy after the scenario;
- SQLite `PRAGMA quick_check` returns `ok`;
- the process-default and proposal-only MCP tool sets remain exact.

Latency is reported but is deliberately not a CI pass gate. A fixed latency
threshold would mostly measure runner load, antivirus behavior, filesystem
speed, and Python process startup rather than accepted-state safety.

## Token and byte accounting

The JSON report measures UTF-8 bytes and ELM's model-neutral token estimate for
the serialized arguments and returned result of every attempted MCP tool call,
including bounded retries and expected refusals. The stable estimator is:

```text
ceil(number of Unicode code points / 4)
```

This is useful for comparing ELM protocol shapes over time. It is **not** a
provider tokenizer and it is not a claim about billed-token savings. The report
therefore always publishes:

```json
{
  "provider_billed_tokens_available": false,
  "provider_billed_tokens": null
}
```

An offline local harness cannot observe agent system prompts, hidden reasoning,
provider-specific tokenization, cache discounts, MCP transport framing, or
provider billing adjustments. A later heterogeneous-host experiment may join
this sanitized ELM report with usage metadata exported by each provider, but it
must keep provider credentials and raw memory bodies outside the artifact.

## Output privacy

The report contains aggregate counts, pass/fail booleans, byte/token estimates,
latency distributions, and bounded outcome categories. It does not emit
proposal IDs, submission IDs, candidate fields, source references, raw MCP
errors, memory bodies, root paths, or terminal transcripts. The fixture itself
is synthetic and ordinary runs never open the user's live ELM root.

## What this pilot does not prove

- It does not measure whether a language model chose a useful fact to propose.
- It does not measure real provider-billed tokens or end-to-end conversation cost.
- It does not emulate separate operating-system principals or hostile machines.
- It does not authenticate an agent, actor label, or human reviewer.
- It does not authorize acceptance, rejection, supersession, deletion, recovery,
  synchronization, identity changes, or any other accepted-state mutation.

## First Windows soak finding

The first repeated Windows run exposed two real sharing races that the earlier
single-operation tests did not reproduce:

1. A waiting process could briefly hold `writer.lock` open for reading while
   the owner tried to unlink it, causing `WinError 32` and leaving a dead lock.
   Lock release now retries for a bounded interval, re-reads the ownership token
   before every delete attempt, distinguishes a definitely missing record from
   an unreadable one, and fails closed on unreadable or foreign-token state.
2. Antivirus or another reader could briefly deny `os.replace` while a flushed
   transaction-journal temporary file was promoted, causing `WinError 5` after
   canonical changes had been written. Atomic replace now retries only
   `PermissionError` for a bounded interval; other failures still surface
   immediately and the existing recovery transaction remains authoritative.

Targeted regression tests inject both sharing violations. Persistent access
denial still fails. Under ELM's cooperative local-writer boundary, replacing or
deleting a path never proceeds after an ownership-token mismatch. This is not
an atomic compare-and-delete primitive against a hostile same-user process that
can replace filesystem entries between the token check and unlink.

The next evidence-producing step after this harness is a heterogeneous-agent
pilot against disposable roots. Phase 5B remains gated on selecting a verifier
that is genuinely outside the proposing agent's authority.

That next step is now implemented as an opt-in local harness and documented in
[HETEROGENEOUS_AGENT_PILOT.md](HETEROGENEOUS_AGENT_PILOT.md). It preserves the
Phase 5A authority boundary and does not turn provider telemetry into a
cross-provider token comparison.
