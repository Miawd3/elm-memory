# ELM — External Local Memory

Local, inspectable memory for coding agents. Markdown stays canonical; a disposable SQLite FTS5 index finds and packs only the context an agent needs.

ELM works with Codex, Claude, Gemini, Cursor, and other terminal- or MCP-capable agents. It does not require a hosted service, model API, embeddings, or a database daemon.

## Why use it?

- **You own the memory.** Durable knowledge is ordinary Markdown in a folder you choose.
- **Context stays bounded.** `elm context` has a hard deterministic token estimate instead of loading an ever-growing chat archive.
- **Sources stay visible.** Results include paths, headings, status, authority labels, and stable section locators.
- **The index is disposable.** Delete `.elm/index.sqlite` at any time and rebuild it from Markdown.
- **Agent-independent.** The same CLI and MCP surface can be used by different coding agents.
- **Safe defaults.** Backups and `99_archive` are excluded from normal retrieval, and retrieved text is labeled as untrusted data.

## Install

### Windows 10/11 x64

Download `ELM-Memory-1.0.0-windows-x64-setup.exe` from the [latest release](https://github.com/Miawd3/elm-memory/releases/latest) and run it.

The installer is per-user, needs no administrator access, includes its own Python runtime, installs `elm` and `elm-mcp`, and can add them to your user `PATH`.

> The first release is not code-signed. Windows SmartScreen may show an unknown-publisher warning. Verify the file against `SHA256SUMS.txt` before running it.

### Linux

Download `elm-memory-1.0.0-linux-any.tar.gz`, then:

```bash
tar -xzf elm-memory-1.0.0-linux-any.tar.gz
cd elm-memory-1.0.0-linux-any
./install.sh
```

The Linux installer checks for Python 3.11+, `venv`, and SQLite FTS5, then installs the bundled wheel into an isolated per-user environment. Use `./install.sh --with-mcp` when you also want the MCP adapter; that option downloads the MCP SDK and its dependencies.

### Python package

```bash
python -m pip install elm_memory-1.0.0-py3-none-any.whl

# Optional MCP adapter
python -m pip install "./elm_memory-1.0.0-py3-none-any.whl[mcp]"
```

See [Installation](docs/INSTALLATION.md) for checksums, updates, rollback, uninstall, source installation, and current platform support.

## Start from zero

Create a new memory root and make it your default:

```bash
elm init --root ~/elm-memory --project my-project --set-default
```

ELM creates five small Markdown files, an empty archive and backup area, and the first disposable index. It refuses to overwrite an existing path.

Then ask for bounded context:

```bash
elm context "Continue the current project" --budget 700 --project my-project --json
```

Useful follow-up commands:

```bash
elm search "database decision" --project my-project --json
elm read SECTION_KEY --project my-project --json
elm related 20_projects/my-project/PROJECT_HUB.md --project my-project --json
elm status --json
elm doctor --json
```

## How it works

```text
Markdown files (canonical)
          |
          | elm sync / rebuild
          v
SQLite + FTS5 (derived, disposable)
          |
          | search / context / read / related
          v
Bounded, source-linked context packet
          |
          v
Any coding agent
```

The normal loop is deliberately small:

1. The agent calls `status` when freshness matters.
2. It requests a bounded packet with `context`.
3. It expands one exact section with `read`, `outline`, or `related` only when needed.
4. Durable decisions are written back to canonical Markdown, followed by `sync`.

The index lives at `<root>/.elm/index.sqlite`. It contains copies of indexed text, so protect it with the same filesystem permissions as the Markdown root.

Read the [Architecture](docs/ARCHITECTURE.md) for identity, retrieval, concurrency, governed memory, and trust boundaries. Read [Agent integration](docs/AGENT_INTEGRATION.md) for a short universal prompt and MCP configuration.

## MCP profiles

`elm-mcp` is a local stdio adapter over the same CLI JSON contracts. It has no separate ranking or storage logic.

| Profile | Tools | Purpose |
| --- | ---: | --- |
| Default | 7 | Read-only status, retrieval, history, and stats |
| `proposal-only` | 10 | Read tools plus bounded untrusted memory proposals |
| `autonomous` | 8 | Read tools plus leased `agent_curated` memory for allowlisted projects |

The default is read-only. Mutation profiles must be enabled explicitly, are limited to named projects, and do not turn agent inference into verified truth.

## What ELM does not claim

- It is lexical retrieval, not semantic search. Synonyms that share no useful terms may be missed.
- A token estimate is a deterministic local budget, not a promise about a provider's billed tokens.
- Project, namespace, archive, and sensitivity filters are governance controls, not multi-user authentication.
- ELM cannot erase copies already stored in Git history, backups, shell history, or model-provider logs.
- The current Windows installer is unsigned.
- macOS has no tested installer in v1.0. Source installation may work, but it is not an officially supported target yet.

See [Evaluation](docs/EVALUATION.md), [Privacy](docs/PRIVACY.md), and [Security](SECURITY.md) for evidence and limits.

## Development

```bash
python -m venv .venv
python -m pip install -e ".[mcp]"
python -m compileall -q src tests benchmarks scripts packaging
python -m unittest discover -s tests -v
python benchmarks/run_benchmark.py --assert-pass
python benchmarks/run_heterogeneous_pilot.py --validate-only --assert-pass
python benchmarks/run_corpus_size_curve.py --validate-only --assert-pass
python benchmarks/run_holdout_confirmation.py --validate-only --assert-pass
```

Release checks run locally; this repository intentionally has no hosted CI workflow. See [Contributing](CONTRIBUTING.md) for the full local gate.

## License

Apache License 2.0. You may use, modify, and redistribute ELM under the terms in [LICENSE](LICENSE).
