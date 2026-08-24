---
description: forkjoin — run this session as the orchestrator of a fork/join fan-out; first positional argument selects the sub-op.
argument-hint: <start|wave|join|status> [<goal>|<run>]
allowed-tools: Skill, Bash, Read, Write, Edit, Glob, Grep
---

# forkjoin — fork/join fan-out across sessions

```
/forkjoin start <goal>
/forkjoin wave <run>
/forkjoin join <run>
/forkjoin status <run>
```

| Sub-op | One-liner |
|---|---|
| `start` | Decompose the goal into units, layer them into waves, create the run directory, enter orchestrator mode. |
| `wave` | Emit copy-paste worker prompts for the next open wave — independent units only. |
| `join` | Read the state reports, verify terminal status, fold findings into `RUN.md`, close with the full run ledger. |
| `status` | Print the run ledger — every unit, its state, what's left — and the gate line. No side effects. |

If the first arg is missing or not one of `start` | `wave` | `join` | `status`, stop and
ask which sub-op — never default. If the first arg is recognized but the remainder is
missing or malformed, stop and ask for the missing slot, naming the offending value.

This session is the **orchestrator**: it holds the context and never does unit work. Each
unit runs in a **worker** — a separate session the operator opens by pasting a prompt
printed here. The sessions share no memory; the only channel is a state report file on disk.

## Argument rules

1. **Positional arguments only.** Slots separated by a space, in the order given. No `::`
   separator and no `--flag value` on the command surface.
2. **No defaults for any argument.** Missing or unrecognized → stop and ask.
3. **Values are globally unique across slots** — no sub-op token is a legal `<run>`.
4. **Use `|` for alternation.**

### Slots

| Slot | Required | Legal values |
|---|---|---|
| `<sub-op>` | yes | `start` \| `wave` \| `join` \| `status` |
| `<goal>` | yes for `start` | free text — the job to decompose |
| `<run>` | yes for `wave` \| `join` \| `status` | `current` \| `<run-id>` matching `^\d{8}-\d{6}-[a-z0-9-]+$` |

`current` is the run started by `start` in **this** session. If `start` has not run here,
`current` is an error — ask for an explicit `<run-id>`.

## Hard rules

- **Orchestrator emits prompts; orchestrator never spawns workers.** No `Agent`, no
  `Workflow`, no background task substitutes for a worker session.
- **Prompts go in chat, copy-paste ready** — never written to a file the operator has to
  open.
- **Only independent units are emitted.** A unit whose parent has not reported `done` is
  withheld until the next wave.
- **The gate is hard.** A missing report file, or a report without a terminal `status:`
  line, blocks the next wave. Never "emit with a warning".
- **Decompose for maximum width.** Wall-clock is the target. Split whenever write targets
  are disjoint; depend only on a real data dependency. Depth is the cost — every wave
  boundary is a serial round-trip — so a one-unit wave is a smell, and merging units to
  tidy the table is a regression.
- **Every emitted prompt carries the parallelism clause.** Parallelism is two-layer: waves
  across sessions, plus each worker going wide inside its own lane (batched tool calls, fan-
  out searches, subagents for independent legs). The clause carries the quality gate with
  it — serialize ordered steps, never thin verification for speed, report per-item results.
- **One exception outranks speed:** two units sharing a write target never run in the same
  wave. Split so the targets become disjoint; add a dependency only if that is impossible.

- **Every prompt is labelled `W<wave>-P<index>/<count>`**, derived by `orchestrator.py`,
  never minted by hand. The label plus the unit id and a short title is the prompt's **first
  line** — it is the operator's place-keeper while pasting, and it becomes the worker
  session's tab title. Prose on line one makes every tab read identically.
- **`RUN.md` is the orchestrator's durable memory.** Goal, constraints, decisions, findings
  — written at `start`, appended at every `join`, re-read at the top of every sub-op. A long
  orchestrator session degrades; the file does not. The unit table is not duplicated into
  it.

## Run layout

```
<cwd>/tmp/runs/<run-id>/units.json          # manifest
<cwd>/tmp/runs/<run-id>/RUN.md              # orchestrator's durable memory
<cwd>/tmp/runs/<run-id>/reports/<unit>.md   # one per unit, written by its worker
```

## Execution

1. Parse `$ARGUMENTS` against the slot table. Missing sub-op → ask which. Missing `<goal>`
   → ask what the job is. Missing `<run>` → ask, offering `current` when `start` ran here.

2. Invoke the `forkjoin` skill via the Skill tool so its `SKILL.md` — decomposition
   rules, manifest schema, report schema, prompt template, gate semantics — is in context,
   then follow its procedure for the chosen sub-op.

3. `start`: confirm the restated goal, decompose into similar-sized units with explicit
   `write_targets` and `depends_on`, run `orchestrator.py init` then `orchestrator.py plan`,
   print the unit table, and stop — do not emit wave 1 in the same turn.

4. `wave` | `join` | `status`: run `orchestrator.py scan <run-dir>` first. `wave` emits only
   when the gate line reads `GATE: OPEN`; otherwise print the gate output verbatim and stop.

5. `join` and `status` close with the **run ledger** — a markdown table carrying every unit
   in the manifest: label, unit, wave, state (copied from `scan` unchanged), and one line of
   what happened or what's left. Then `done x/n / outstanding y / blocked z`, the gate line,
   and the single next action.

6. Report the whole set — failures and missing units included, never only the successes.
