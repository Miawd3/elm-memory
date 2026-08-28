# Corpus-size curve

Status: executable local evidence harness; Phase 5B is outside the active roadmap

Date: 2026-08-27

## Purpose

The corpus-size curve tests the narrow scaling claim left open by the first
heterogeneous-agent pilot: at what active-corpus size, if any, does bounded ELM
retrieval use fewer provider-reported tokens than placing the complete active
Markdown corpus in the initial prompt?

The experiment is deliberately capable of returning **no crossover**. A clean
run proves that the measurement was valid; it does not require ELM to win.

## Scale model

The curve starts from the six active Markdown documents in the synthetic Orion
fixture. It adds deterministic, oracle-free distractor documents until each
configured model-neutral corpus-token target is reached. The defaults are:

- 2,000 estimated corpus tokens;
- 8,000 estimated corpus tokens;
- 32,000 estimated corpus tokens;
- 128,000 estimated corpus tokens.

ELM's stable character estimator defines these target sizes. Actual generated
sizes and UTF-8 bytes are reported separately. Every larger corpus contains the
smaller corpus byte-for-byte plus additional distractors, so content drift does
not masquerade as a size effect. Each size receives an independent disposable
index and is rebuilt before use.

The distractors contain no expected answer, source path, expected heading, or
normalized significant vocabulary from any benchmark question or lookup query.
The evaluator oracle remains in
`benchmarks/heterogeneous_cases.json`, outside provider schemas and outside the
ELM/no-memory prompts.

## Schedule and isolation

Every `(route, size, case, repeat)` cell is a pair containing `elm` and
`full_corpus`. Repeats must be even. Condition order flips within each exact
cell, size order alternates forward and reverse, and case order rotates between
repeats. This limits simple order and warm-cache bias without pretending that
provider caches can be disabled or normalized.

Each provider call receives a separate empty workspace. Only the ELM MCP
subprocess receives the matching temporary root. The existing heterogeneous
pilot owns response validation, provider-native usage parsing, strict streamed
tool provenance, output sanitization, and canonical Markdown immutability.

Comparisons never cross route, case, repeat, or corpus size. Provider-native
counters retain their original meanings and remain marked as not comparable
between providers. Initial-prompt estimated tokens are reported separately from
provider end-to-end usage.

## Crossover rules

The report distinguishes a raw ratio from a benchmark-qualified crossover.
For one size to qualify:

1. every planned pair at that route and size must pass quality, telemetry, and
   provenance checks;
2. at least five non-tied comparable pairs must exist;
3. the median ELM/full-corpus ratio must be below `1.0`;
4. a one-sided exact sign test must be at or below the configured alpha, which
   defaults to `0.05`.

Pair direction and the median are calculated from the raw integer counters, not
from display-rounded ratios. The exact sign-test numerator and denominator are
reported alongside its floating representation. A crossover is suppressed
unless the complete planned pair identity set is present and every report-wide
integrity check passes, including schedule completion and canonical Markdown
immutability. Partial or timed-out results remain diagnostic only.

A crossover is reported only when the qualifying result persists at the next
larger tested size and every still-larger tested size. This is bounded evidence
for this synthetic workload and host route, not a universal provider or memory
claim. With the default one-case/two-repeat calibration, a curve can be valid
but cannot qualify a crossover; use all three cases and at least two repeats to
reach the minimum evidence count.

## Safe operation

Static validation creates only small disposable roots and calls no model:

```bash
python benchmarks/run_corpus_size_curve.py --validate-only --assert-pass
```

An initial bounded Codex calibration is 16 provider calls:

```bash
python benchmarks/run_corpus_size_curve.py \
  --execute \
  --routes codex \
  --case-ids orion_storage \
  --target-corpus-tokens 2000 8000 32000 128000 \
  --repeats 2 \
  --max-runs 16 \
  --max-total-seconds 7200 \
  --max-prompt-estimated-tokens 250000 \
  --assert-pass
```

A claim-capable five-size Codex matrix uses all three cases, two repeats, and
60 calls. Build the exact synthetic roots and inspect the prompt envelope first;
`--plan-only` performs no provider calls:

```bash
python benchmarks/run_corpus_size_curve.py \
  --plan-only \
  --claim-capable \
  --routes codex \
  --all-cases \
  --target-corpus-tokens 2000 8000 32000 128000 192000 \
  --repeats 2 \
  --codex-model gpt-5.6-sol \
  --codex-reasoning-effort low \
  --max-runs 60 \
  --max-total-seconds 7200 \
  --max-prompt-estimated-tokens 200000 \
  --fail-fast \
  --assert-pass
```

Run the reviewed plan by replacing `--plan-only` with `--execute`; keep every
other argument identical. `--claim-capable` prevents a crossover from being
reported unless every selected route has an explicit model and Codex also has
an explicit reasoning effort. Without that flag, successful calibration output
remains diagnostic and its crossover interpretation is
`claim_mode_not_enabled`.

The 2026-08-27 local preflight produced 60 calls and 30 exact pairs. The fifth
root contained 197 active documents and 192,573 estimated corpus tokens; its
largest full-corpus initial prompt estimate was 192,792. The selected Codex
model metadata advertised a 272,000-token context window with a 95% effective
window (258,400), leaving 65,608 estimated tokens of conservative headroom for
host instructions, tools, and output. This is a run-specific safety rationale,
not a portable promise about future model versions.

At each size the matrix has six pairs. With a one-sided exact sign test, all six
must favor ELM to pass alpha `0.05`: six of six gives `p = 0.015625`, while five
of six gives `p = 0.109375`. A crossover still requires a qualifying size plus
the next and every larger tested size, so a 128,000 crossover needs both the
128,000 and 192,000 cells to qualify.

The scheduled `case × repeat` pair is the sign-test unit. Because the two
repeats reuse each of three case templates, this is deliberately labeled
bounded benchmark-panel evidence, not population inference. It does not
generalize to unseen tasks, another model, another reasoning effort, or another
CLI version; that broader claim requires a separately frozen holdout panel.

Answer scoring remains deterministic: the response must copy the single
supporting sentence from the synthetic evidence. Comparison is exact after
case-folding and removal of trailing periods and whitespace; no paraphrase or
semantic model judge is accepted. The oracle sentence is absent from the ELM
and no-memory prompts and from the response schema.

Real calls always require `--execute`. The harness refuses odd repeat counts,
unbounded run matrices, prompts above the configured estimate cap, per-run
timeouts above 900 seconds, and total execution budgets above six hours. Direct
Claude Code retains its separate per-run USD cap. Authenticated provider runs
remain local and must not run in hosted CI.

`--fail-fast` is recommended for expensive claim-capable matrices. It stops
after the first failed quality, telemetry, provenance, or execution cell; the
incomplete schedule then fails the global integrity gate and cannot produce a
claim.

Before each provider call, its timeout is reduced to the remaining total budget.
For Antigravity, the dependency runner's ten-second subprocess grace is
subtracted before dispatch, and a call is not started when that grace no longer
fits. The total clock is checked again after every call; an overrun fails the
global integrity gate even if the provider returned a valid answer.

Antigravity routes retain the exact temporary seven-tool permission procedure
documented in [HETEROGENEOUS_AGENT_PILOT.md](HETEROGENEOUS_AGENT_PILOT.md).
Correct answers with incomplete telemetry or unexpected tool activity are not
accepted as curve evidence.

## First bounded calibration

The first local Codex calibration completed on 2026-08-27 with one case, two
repeats, and 16/16 passing provider calls. Every response passed answer and
evidence checks, every ELM run used only the approved read surface, every
full-corpus run used no tools, and all provider-native usage records were
complete. The paired metric was provider-reported input plus output tokens for
the complete CLI run; cached-input counters remain reported separately and are
not subtracted.

| Target | Actual corpus | Active docs | Median ELM/full-corpus ratio | Exact sign p | Qualified claim |
| ---: | ---: | ---: | ---: | ---: | :---: |
| 2,000 | 2,959 | 8 | 4.028718 | 1.00 | No |
| 8,000 | 8,978 | 14 | 3.926756 | 1.00 | No |
| 32,000 | 32,053 | 37 | 2.458558 | 1.00 | No |
| 128,000 | 128,365 | 133 | 0.840618 | 0.25 | No |

At the largest measured corpus, both pairs favored ELM: the exact paired totals
were `89,957` versus `107,001` and `89,937` versus `107,001` tokens. This is a
useful calibration signal, not a crossover claim. Two pairs are below the
five-pair minimum, the exact sign result is not significant, and there is no
larger measured size at which persistence could be established. The defensible
result is therefore **no benchmark-qualified crossover observed**. A later
claim-capable run should add all cases and at least one larger target rather
than reinterpret this calibration.

During the first attempt, Codex emitted successful answers together with
diagnostic `error` items in its JSONL stream. The provenance parser had treated
that non-call item type as an unknown tool, so the global gate correctly failed
closed. The parser now distinguishes diagnostic items from actual tool calls;
command execution, file changes, web/image tools, unapproved MCP calls, and
unknown call-like items remain disqualifying, while a non-zero CLI exit remains
an execution failure.

## Five-size claim-capable panel

The five-size Codex panel completed on 2026-08-27 from commit `dd18ae4`, using
`codex-cli 0.149.0`, explicit model `gpt-5.6-sol`, and reasoning effort `low`.
All 60/60 scheduled calls and 30/30 exact pairs passed answer, evidence,
provider-usage, tool-provenance, schedule-completeness, and canonical-Markdown
immutability gates. The run took 724.488 seconds.

Two earlier fail-closed calibration attempts are excluded from these numbers.
They exposed an evaluation-contract defect: a correct complete supporting
sentence failed an oracle that expected only its shorter answer phrase. Before
the accepted run, the protocol was changed to require the single supporting
sentence verbatim, the deterministic exact matcher was restored, the offline
suite passed, and a separate three-call ELM smoke passed all three cases. No
failed provider cell was silently reclassified or counted in the accepted
panel.

| Target | Actual corpus | Active docs | Median ELM/full-corpus ratio | Below full corpus | Exact sign p | Qualified cell |
| ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 2,000 | 2,959 | 8 | 4.322003 | 0/6 | 1.000000 | No |
| 8,000 | 8,978 | 14 | 3.438687 | 0/6 | 1.000000 | No |
| 32,000 | 32,053 | 37 | 2.160081 | 0/6 | 1.000000 | No |
| 128,000 | 128,365 | 133 | 0.895041 | 5/6 | 0.109375 | No |
| 192,000 | 192,573 | 197 | 0.599836 | 6/6 | 0.015625 | Yes |

The final and only accepted interpretation is **no benchmark-qualified
crossover observed**. The 192,000 cell qualifies, but the preregistered
persistence rule requires at least two consecutive qualifying sizes. At
128,000, ELM was lower in five of six pairs, so that cell misses the exact-sign
threshold even though its median ratio is below one. This locates a strong
candidate region between the two largest sizes; it does not establish a
crossover point.

Across the complete size mix, the 30 ELM runs reported 2,916,383 input-plus-
output tokens and the 30 full-corpus runs reported 2,086,186. These totals are
useful for experiment budgeting but are not the crossover statistic because
they pool different corpus sizes. The bounded result applies only to this
synthetic three-case panel, model, reasoning effort, CLI version, and provider
telemetry basis.

## Interpretation boundary

The generated roots and workspaces are evaluation-integrity controls, not an
OS security boundary against a hostile local process. Provider nondeterminism,
account-level prompt caching, model updates, and vendor-specific token semantics
remain real limitations. Counterbalancing and within-route pairing reduce these
effects; they do not erase them.

The curve evaluates read-only retrieval efficiency only. It does not widen the
MCP surface, enable accepted-state mutation, train a model, add embeddings, or
advance any accepted-authority or destructive mutation surface.

The independent next stage is frozen in
[HOLDOUT_CONFIRMATION.md](HOLDOUT_CONFIRMATION.md). It uses six unseen cases
across three projects, project-scoped near-miss growth, case-level statistics,
no-memory leakage controls, and a context-safe 208k persistence point. Its
preregistered 78-call Codex run later qualified all three holdout sizes and
confirmed sustained large-corpus token advantage within that independent
synthetic panel. The holdout result does not retroactively convert this
development curve into population evidence or identify an exact universal
crossover.
