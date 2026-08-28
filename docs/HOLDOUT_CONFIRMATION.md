# Holdout confirmation panel

Status: completed confirmatory provider panel; Phase 5B is outside the active roadmap

Date: 2026-08-27

## Purpose

The first claim-capable corpus-size curve used three questions from one Orion
document. Its large distractor set lived outside Orion while the ELM arm used
`project='orion'`. That experiment validly measured the cost of project-scoped
retrieval against a growing global prompt, but it did not independently test
unseen tasks or retrieval ambiguity that grows inside the selected project.

`benchmarks/run_holdout_confirmation.py` addresses that limitation without
reinterpreting or overwriting the development result. It is a separately
versioned, frozen confirmation panel. The provider must not be run until the
static validation and exact plan both pass.

## Frozen panel

The evaluator-only oracle is `benchmarks/holdout_cases.json`. It contains six
previously unused synthetic cases from six source files and three projects:
Amber, Mosaic, and Zephyr. The cases span format/identifier, numeric cadence,
compound numeric policy, algorithm selection, network configuration, and
compound naming questions.

The generated corpora have three fixed estimated-token targets:

- 128,000: lower anchor;
- 192,000: candidate large-corpus confirmation point;
- 208,000: context-safe persistence point.

The roots are nested and deterministic. Every larger root contains every
Markdown byte from the smaller root plus new distractors. Distractors are
distributed before and between target documents, and a bounded subset grows
inside each selected project with query-adjacent vocabulary but no accepted
value. This tests retrieval ambiguity as well as global prompt growth.

Target files occupy early, middle, late, and final-corpus positions. The
Zephyr snapshot case is in `20_projects/zephyr/ZZZ_TAIL_CANARY.md`, which must
remain the final active document at every size. Backups and `99_archive` remain
excluded.

The panel manifest hashes the base fixture, case file, fixed target list,
statistical contract, generator bands, filler phrase, and near-miss cadence.
Each generated size also receives its own complete Markdown manifest hash.
Changing any oracle, fixture, or generator input after provider output is seen
invalidates this panel and requires a new protocol version.

## Schedule and controls

For each case and size, two technical repetitions compare ELM with the complete
active corpus. Each repetition is an exact pair; condition order flips AB/BA.
Size order reverses and case order rotates across repetitions. The paired
matrix contains 72 calls.

One no-memory call per case runs first, adding six leakage controls and bringing
the frozen maximum to 78 provider calls. A no-memory response passes only when
it returns the prescribed insufficient-evidence object, with no tools. Any
control failure invalidates the complete panel rather than being discarded.

Every provider call uses a separate empty workspace. ELM calls receive only
the disposable root for their size and the seven read-only MCP tools. Full
corpus and no-memory calls must use no tools. Provider-native usage,
tool provenance, answer/evidence quality, schedule completeness, and canonical
Markdown immutability all fail closed.

## Statistical contract

The independent unit is the **holdout case**, not a repeated provider call.
For every case and size, the estimand is:

```text
median over the two repetitions of
log(provider-reported ELM tokens / provider-reported full-corpus tokens)
```

The displayed case ratio is the exponential of that value. A size qualifies
only when all of the following are true:

1. all six case summaries contain both planned, passing, comparable repeats;
2. all six case-level ratios are below `1.0`;
3. the one-sided exact sign test across the six cases is at most `0.05`;
4. the median case-level ratio is at most `0.90`.

With six cases, all six must favor ELM; that produces exact `p = 1/64 =
0.015625`. Repeats reduce within-case noise but never increase the sign-test
sample size.

The narrow result “sustained large-corpus token advantage confirmed” is allowed
only when both 192k and 208k qualify and every report-wide integrity check
passes. The 128k point is an anchor:

- if it also qualifies, the tested onset is only known to be at or below 128k;
- if it does not and both larger cells qualify, the tested onset is bracketed
  between the 128k and 192k test points;
- otherwise, sustained advantage is not confirmed.

This remains evidence for one synthetic holdout panel, Codex route, model,
reasoning effort, CLI version, and provider telemetry basis. The cases were
deliberately designed, not randomly sampled from a real task population, so
broader population generalization is not supported.

## Context safety

The confirmation matches the development route exactly:

- `codex-cli 0.149.0`;
- model `gpt-5.6-sol`;
- reasoning effort `low`.

The locally advertised context window was 272,000 tokens with a 95% effective
ceiling of 258,400. The frozen harness requires at least 45,000 estimated
tokens of headroom. The accepted local preflight produced a largest initial
prompt estimate of 208,531 and therefore 49,869 tokens of headroom. This is why
208k was selected instead of 216k or 224k.

The estimator is deterministic but is not the provider tokenizer. Headroom,
the final-document canary, successful full-corpus answers, and complete
provider telemetry are complementary safeguards; none alone proves that a
future provider or model version handles the same envelope.

## Safe commands

Static validation builds only small disposable roots and never calls a model:

```bash
python benchmarks/run_holdout_confirmation.py \
  --validate-only \
  --assert-pass
```

The exact full preflight builds all three disposable roots, resolves every
expected section, verifies that each 700-token ELM context packet selects it,
and measures the prompt envelope without provider calls:

```bash
python benchmarks/run_holdout_confirmation.py \
  --plan-only \
  --codex-model gpt-5.6-sol \
  --codex-reasoning-effort low \
  --max-runs 78 \
  --max-total-seconds 10800 \
  --max-prompt-estimated-tokens 210000 \
  --assert-pass
```

Only after review, run the frozen provider matrix by replacing `--plan-only`
with `--execute` and adding `--fail-fast`. Every other argument must remain
identical. The harness rejects execution without the pinned model, effort,
run cap, prompt cap, and fail-fast behavior.

## Completed confirmation result

The provider panel completed on 2026-08-27 from preregistered commit `918fa69`
and panel manifest
`d7c12af83621e2c6e82e157383772a7717cef1b580763dfc9f22d7b424e0ce09`.
Both hosted CI runs for the frozen commit passed before execution. The local
matrix used `codex-cli 0.149.0`, `gpt-5.6-sol`, and reasoning effort `low`.

All 78/78 calls passed: six no-memory controls, 36 ELM calls, and 36 complete-
corpus calls. Every answer/evidence check, provider-usage record, tool-
provenance audit, exact pair, case summary, canonical-Markdown manifest, target
placement check, and tail-canary gate passed. The run did not abort and took
984.232 seconds.

| Target | Actual corpus | Active docs | Median case-level ELM/full ratio | Cases below full | Exact sign p | Qualified |
| ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 128,000 | 128,812 | 132 | 0.714786 | 6/6 | 0.015625 | Yes |
| 192,000 | 192,599 | 193 | 0.503037 | 6/6 | 0.015625 | Yes |
| 208,000 | 208,291 | 208 | 0.467141 | 6/6 | 0.015625 | Yes |

The preregistered conclusion is therefore **sustained large-corpus token
advantage confirmed for this independent synthetic holdout panel**. Both
primary targets, 192k and 208k, qualified. The 128k anchor also qualified, so
this experiment establishes only that the tested onset is at or below 128k; it
does not identify an exact crossover point.

For run budgeting, provider-reported input-plus-output totals were 3,453,791
tokens for ELM, 6,313,543 for complete-corpus calls, and 119,369 for no-memory
controls. The paired-arm pooled ratio is 0.547045, but the preregistered
statistic remains the case-level ratio and exact sign test at each size, not a
total pooled across different corpus sizes.

This result supersedes the development panel only for the narrow question it
was designed to confirm. It does not establish an exact universal crossover,
real-world task quality, latency superiority, cost equivalence across vendors,
or generalization to another model, reasoning effort, CLI version, corpus, or
provider telemetry definition.
