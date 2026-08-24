# claude-fork-join

Source repo for the `forkjoin` Claude Code skill: a fork/join orchestrator that decomposes
a job into units, layers them into parallel-safe waves, and prints copy-paste prompts for
separate Claude Code sessions ("workers") that write state report files back.

This repo **ships a skill**. There is no application, no build, no test suite. The artifacts
are three text files that get copied into a Claude Code config directory.

## Layout

| Path | Role |
|---|---|
| `skills/forkjoin/SKILL.md` | Model half — decomposition rules, prompt template, manifest + report schemas, gate semantics, orchestrator-mode persistence. |
| `skills/forkjoin/orchestrator.py` | Deterministic half — `init` / `plan` / `scan`. Run scaffolding, wave layering from the dependency graph, write-target collision detection, report gating. Stdlib only, Python 3, no deps. |
| `commands/forkjoin.md` | `/forkjoin` slash command; routes into the skill via the Skill tool. |
| `README.md` | User-facing install + usage. |
| `assets/avatar.svg` | Avatar source. Re-render the PNG with `convert assets/avatar.svg -depth 8 -define png:color-type=6 assets/avatar.png` after editing; never hand-edit the PNG. |

Install target: `~/.claude/skills/forkjoin/` and `~/.claude/commands/forkjoin.md`
(or wherever `CLAUDE_CONFIG_DIR` points).

## Design invariants — do not break these when editing

- **Split of responsibility.** The model owns judgment (what the units are, what each worker
  needs to know). `orchestrator.py` owns what must not be guessed (wave order, write-target
  collisions, whether the gate opens). Never move a gating or layering decision into prose,
  and never put unit-content judgment into the script.
- **The orchestrator emits prompts; it never spawns agents.** No `Agent`, no `Workflow`, no
  background tasks as a substitute for a worker session. Any edit that adds auto-spawning
  changes the product.
- **The gate is hard.** Missing report file, or a report with no terminal `status:` line,
  blocks the next wave. No "emit with a warning" path.
- **Only cross-session channel is the report file** on disk. Sessions share no memory.
- **Positional arguments, no defaults.** Missing/unrecognized slot → stop and ask, naming
  the offending value. No `--flag value`, no `::` separator on the command surface.
- **Prompts print in chat, copy-paste ready** — never written to a file the operator opens.
- **Labels `W<wave>-P<index>/<count>` come from `orchestrator.py`**, never hand-minted, and
  are the prompt's first line (they become the worker session's tab title).

## Three-file coherence

`SKILL.md`, `commands/forkjoin.md`, and the `description:` frontmatter in `SKILL.md` all
restate the sub-op contract, the slot table, and the hard rules. A change to argument
grammar, sub-op set, or run layout must land in **all** of them plus `README.md` in the same
change. Drift between them is the main defect class in this repo.

Shared constants that must stay in sync between `SKILL.md` prose and `orchestrator.py`:
`TERMINAL_STATUSES = done|blocked|failed`, `RUNNING_STATUS = in-progress`,
`DONE_MARKER = "job is done."`, `STOP_MARKER = "job stopped"`,
`RUN_ID_RE = ^\d{8}-\d{6}-[a-z0-9-]+$`.

## Run layout produced at runtime (not in this repo)

```
<cwd>/tmp/runs/<run-id>/units.json
<cwd>/tmp/runs/<run-id>/RUN.md
<cwd>/tmp/runs/<run-id>/reports/W<wave>-P<idx>-<unit-id>.md
```

`tmp/` is gitignored. `<run-id>` is minted only by `orchestrator.py init` — never by hand;
two runs sharing an id silently merge their reports.

## Verifying a change

No test suite. Exercise the script directly against a scratch root:

```bash
python3 skills/forkjoin/orchestrator.py init tmp/runs some-goal-slug
python3 skills/forkjoin/orchestrator.py plan tmp/runs/<run-id>/units.json
python3 skills/forkjoin/orchestrator.py scan tmp/runs/<run-id>
```

`plan` mutates the manifest in place; `scan` prints the status table and the `GATE:` line.
Errors exit 2 with `ERROR: ...` on stderr.

## Conventions

- Markdown files wrap at ~92 columns; keep the existing table-heavy, rule-list style.
- No `Co-Authored-By:` footers in commits.
- Commit prefixes follow conventional commits (`feat:`, `fix:`, `docs:`).
