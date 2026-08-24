---
name: hack-master
description: |
  Run this session as the master of a master/slave fan-out: hold the context, decompose a big job into units of work, group them into waves that are safe to run in parallel, and emit copy-paste prompts for slave sessions that each write a state report file back. First positional argument selects the sub-op: `start` | `wave` | `join` | `status`; the remaining tokens are parsed per sub-op contract.

  - `start <goal>` — ingest context, create the run directory, decompose the goal into a unit table, layer the units into waves, and flip master mode on for the rest of the session.
  - `wave <run>` — emit the copy-paste slave prompts for the next open wave. Independent units only; dependent units are withheld until their parents report `done`.
  - `join <run>` — read every state report, verify each carries a terminal status line, and report which units are done, missing, blocked, or failed.
  - `status <run>` — one-screen snapshot of the run: units, waves, report states, and which wave is currently gated.

  Use when the user asks to "run this as master/slave", "split this job into parallel sessions", "give me prompts I can run in parallel", "group these tasks into waves", "which of these can run in parallel", "all slaves are done, proceed", "join the reports", or invokes `/hack-master`.

  If the first arg is missing or not one of `start` | `wave` | `join` | `status`, stop and ask which sub-op — never default. If the first arg is recognized but `<goal>` or `<run>` is missing or malformed, stop and ask for that slot, naming the offending value. No argument has a default.
metadata:
  role: orchestrator
  scope: session-persistent
  routed: true
  sub-ops: start, wave, join, status
  triggers: master slave, parallel sessions, fan out, units of work, wave, state report, orchestrate, decompose, run in parallel
---

# hack-master — master/slave fan-out across sessions

```
/hack-master start <goal>
/hack-master wave <run>
/hack-master join <run>
/hack-master status <run>
```

| Sub-op | Anchor | One-liner |
|---|---|---|
| `start` | [`## start`](#start) | Decompose the goal into units, layer into waves, create the run directory, enter master mode. |
| `wave` | [`## wave`](#wave) | Emit copy-paste slave prompts for the next open wave — independent units only. |
| `join` | [`## join`](#join) | Read the state reports, verify terminal status, report done / missing / blocked / failed. |
| `status` | [`## status`](#status) | Snapshot the run without emitting anything. |

If the first arg is missing or not one of `start` | `wave` | `join` | `status`, stop and
ask which sub-op — never default.

## Operating model

This session is the **master**. It holds the context, owns the decomposition, and never
does unit work itself. Every unit of work runs in a **slave** — a separate Claude Code
session the operator opens by pasting a prompt this skill printed.

The two sessions share no memory. The only channel between them is a **state report
file** on disk, written by the slave at a path the master assigned. Everything this skill
does is built on that one fact.

- **Master emits prompts. Master does not spawn slaves.** No `Agent`, no `Workflow`, no
  background task stands in for a slave session. The operator runs them, so the operator
  keeps per-session isolation, inspection, and cost control.
- **Prompts are printed in chat, in copy-paste form.** Never write the prompts to a
  markdown file and point at it — the operator copies them out of the transcript.
- **Only independent units are emitted.** A unit whose parent has not reported `done` is
  withheld until the next wave. This is not advice; it is the gate.
- **The gate is hard.** A missing report file is not success. A report without a terminal
  `status:` line is not success. Either one blocks the next wave.

## Files in this skill

| File | Role |
|---|---|
| `master.py` | Deterministic half — run scaffolding, wave layering, write-target collision detection, report gating. |
| `SKILL.md` | This file — decomposition rules, prompt template, report schema, master-mode persistence. |

The model owns judgment (what the units are, what each slave needs to know). The script
owns everything that must not be guessed (wave order, collisions, whether the gate opens).

## Argument rules

1. **Positional arguments only.** Slots separated by a space, in the order given. No `::`
   separator, no `--flag value`.
2. **No defaults for any argument.** Missing or unrecognized → stop and ask.
3. **Values are globally unique across slots.** No sub-op token is a legal `<run>`.
4. **Use `|` for alternation.**

### Slots

| Slot | Required | Legal values |
|---|---|---|
| `<sub-op>` | yes | `start` \| `wave` \| `join` \| `status` |
| `<goal>` | yes for `start` | free text — the job to decompose, one sentence or a pasted brief |
| `<run>` | yes for `wave` \| `join` \| `status` | `current` \| `<run-id>` matching `^\d{8}-\d{6}-[a-z0-9-]+$` |

`current` is the run started by `start` in **this** session. If `start` has not run here,
`current` is an error, not an empty run — ask for an explicit `<run-id>` and offer to list
the run root.

## Run layout

Rooted at `tmp/master/` inside the session's working directory, one directory per run:

```
<cwd>/tmp/master/<run-id>/
├── units.json          # the manifest — master writes, master.py layers
├── RUN.md              # the master's durable memory — see ## Run log
└── reports/
    ├── W1-P1-<unit-id>.md   # one per unit, written by that unit's slave;
    ├── W1-P2-<unit-id>.md   # label-first so the listing sorts by wave and prompt
    └── …
```

`<run-id>` is `<YYYYMMDD>-<HHMMSS>-<goal-slug>`, minted by `master.py init`. Never invent
one by hand — two runs with the same id silently merge their reports.

## Decomposing for width

Wall-clock is the operator's optimization target. Two layers of parallelism serve it, and
both are used to the maximum the work allows:

1. **Width** — units per wave, each a separate slave session, all running at once.
2. **Depth-inside-a-unit** — each slave fanning out over its own lane concurrently.

**Depth is the cost; width is free.** Every wave boundary is a serial round-trip through
the operator — paste prompts, wait for the slowest slave, come back, join. Two waves of
four beat four waves of two by roughly half the wall-clock even though the unit count is
identical. So: **split aggressively, and depend sparingly.**

Rules for the split:

- **Split whenever write targets are disjoint.** Two files, two repos, two services, two
  docs — two units. Do not merge them because they are "the same kind of work" or because
  a single slave "could handle both". A merged unit is a serialized unit.
- **Prefer many small units over few large ones.** A long tail is what blocks the join, so
  breaking up the biggest unit shortens the wave more than any other edit. Similar size
  still matters — but reach it by splitting the big ones, never by merging the small ones.
- **Depend only on a real data dependency** — unit B needs something unit A writes or
  discovers. "Logically follows", "same area", "cleaner in order", and "safer that way"
  are not dependencies. Each spurious dependency buys a whole extra wave.
- **A one-unit wave is a smell.** It costs a full round-trip for zero parallelism. Either
  split that unit or drop the dependency that stranded it. `master.py plan` flags these.
- **Attack the critical path.** `plan` prints the longest dependency chain — it is the
  run's depth floor no matter how many units exist. Shortening it is the only edit that
  makes the run faster once width is already high.
- **Every emitted prompt tells its slave to fan out internally**, so a unit with several
  independent legs still runs them concurrently. Width across sessions and concurrency
  inside a session compound; use both.

The one hard limit, which speed does not override:

- **Never put two units that share a write target in the same wave.** Not "unlikely to
  collide" — disjoint, or serialized. Concurrent writers to one file, branch, database,
  bucket, or queue is a corruption bug, and a corrupted run costs more wall-clock than the
  serial version it replaced. `master.py plan` refuses this and the refusal is not
  negotiable.
- **When independence is genuinely uncertain**, split the unit so the targets *become*
  disjoint. Add a dependency only if that is impossible.

## Prompt labels

Every unit carries a stable label of the form `W<wave>-P<index>/<units in that wave>` —
`W2-P3/7` is the third of seven prompts in wave 2. `master.py` derives it from the
manifest, so the same unit shows the same label in `plan`, in `scan`, in the chat heading,
and inside the prompt itself. **Never mint a label by hand and never renumber**; copy what
the script printed.

The label exists because the operator pastes prompts into many terminal windows in
sequence and needs to know where they stopped, and because a slave session's tab title is
taken from the **opening text of its prompt**. A prompt that starts with prose gives every
tab the same unreadable title. So the first line of every emitted prompt is the label and
the unit id, and nothing else.

The same label carries through to the report the slave writes back — filename
`<label>-<unit-id>.md`, a `label:` frontmatter field, and the report's `# ` heading — so
prompt, tab, and report file all read the same identifier, and `ls reports/` sorts into the
order the prompts were pasted.

## Run log

`RUN.md` in the run directory is the master's durable memory. The master session is
long-running by design, which makes its context the most fragile thing in the run — it
holds the reasoning that no report and no manifest records.

Write to it whatever would be expensive to re-derive:

- the confirmed goal, in one line;
- constraints and non-goals binding the whole run;
- decisions, with the reason — especially decisions to split, merge, or re-order units;
- findings folded in at each `join`, and what they changed;
- open questions and who resolves them.

Do **not** copy the unit table into it. `units.json` is the manifest; a second copy drifts
and then the master trusts the wrong one.

Update points, all mandatory:

- `start` — after `plan` succeeds, fill Goal, Constraints, and the first Decisions rows.
- `join` — append a `### wave <n>` block under Findings before reporting to chat.
- Any manifest edit — one Decisions row saying what changed and why.

Read `RUN.md` at the top of every sub-op before acting. If it contradicts the master's
own recollection, `RUN.md` wins — it was written when the facts were fresh.

## Manifest schema

`units.json`, authored by the master, layered in place by `master.py plan`:

```json
{
  "run_id": "20260817-184500-ci-hardening",
  "goal": "one-line restatement of the job",
  "units": [
    {
      "id": "backend-auth-gate",
      "title": "Add the lockfile gate to backend-auth",
      "scope": "what the slave must do, 1-3 sentences",
      "non_goals": ["what it must not touch"],
      "context": ["facts the slave cannot derive from the repo"],
      "done_criteria": ["observable, checkable"],
      "write_targets": ["tech/backend-auth/.gitlab-ci.yml"],
      "depends_on": []
    }
  ]
}
```

`wave` is added by `master.py plan`. Do not hand-assign it.

**`write_targets` is the parallelism contract, not a comment.** Independence is decided at
the write-target level, not by how unrelated two units sound. Two units that both write
the same file, branch, database, bucket, or queue are not independent even if their tasks
are. `master.py plan` refuses a wave whose units share a write target.

## State report schema

One file per unit at `<run-dir>/reports/<label-stem>-<unit-id>.md` —
`W1-P3-record-deferred-backlog.md`. The label leads the filename so a directory listing
sorts into wave-and-prompt order and matches, one for one, the prompts the operator pasted.

The **stem** is the label without its `/<count>` suffix: label `W1-P3/7`, filename prefix
`W1-P3`. A slash cannot appear in a filename. `scan` prints the exact report path for every
unit it tells you to emit — copy that path into the prompt rather than composing it.

Mandated verbatim in every slave prompt. `master.py scan` parses the frontmatter and
treats anything else as `MALFORMED`:

```markdown
---
unit: <unit-id>
label: <label>
wave: <n>
status: in-progress
summary: one line, what actually happened
---

# <label> <unit-id> — <short title>

## Progress log
- started: <what this slave is about to do>
- <appended as each step completes>

## Files changed
- path/to/file — what changed

## Findings
- anything the master needs that it could not have predicted

## Blockers
- none

## Next-step hint
- what the dependent units should know

Job is done.
```

`status:` is one of `in-progress` | `done` | `blocked` | `failed`. No fifth value, no prose.
A slave that could not finish writes `blocked` or `failed` with the reason under Blockers —
it does not omit the file.

### Written incrementally, closed by a marker

The report is created **before the work starts**, with `status: in-progress`, and updated as
the slave goes. A session that is closed, interrupted, or killed then leaves the story
behind instead of nothing — the master can see how far it got and what it hit.

That creates a second problem, which the closing marker solves: a crash after the header
would leave `status: done` sitting above a half-written body, indistinguishable from a real
completion on frontmatter alone. So the last thing a slave does is flip the status and write
the closing line:

| Closing line | Written when |
|---|---|
| `Job is done.` | `status: done` |
| `Job stopped: <reason>.` | `status: blocked` or `status: failed` |

`scan` reads the last non-empty line. A terminal status with no closing marker is
`TRUNCATED` — a dead session, never success. `in-progress` shows as `RUNNING` along with the
file's last line, so the master can see what the slave was part-way through.

**Labels move when the manifest is re-planned, filenames do not.** `scan` therefore matches
a report by unit id and accepts any label prefix, reporting `[filed as …]` when the two
disagree. Two files for one unit — a stale label and a current one — is `AMBIGUOUS` and
blocks the gate; delete the stale file rather than guessing which is current.

## Run ledger

The closing artifact of both `join` and `status`: **every unit in the run, in one table**,
so the operator sees at a glance what is finished and what is left without opening a file
or scrolling back through emitted prompts.

Print it last, after the narrative, as a real markdown table — never as prose, never as a
bare list, and never abridged to "the interesting ones". Every unit in the manifest gets a
row, including the ones already done in earlier waves.

| # | Unit | Wave | State | What happened / what's left |
|---|---|---|---|---|
| `W1-P1/3` | `backend-auth-gate` | 1 | done | Lockfile gate added, pipeline green |
| `W1-P2/3` | `backend-media-gate` | 1 | RUNNING | Read 3 of 7 standards files when last written |
| `W1-P3/3` | `backend-user-gate` | 1 | failed | Vault unreachable — needs credentials, then re-emit |
| `W2-P1/1` | `document-gates` | 2 | not started | Blocked on wave 1 |

Column rules:

- **`#`** — the label from `master.py`, verbatim, never renumbered.
- **State** — the state `scan` reported for that unit, unchanged: `done`, `blocked`,
  `failed`, `RUNNING`, `TRUNCATED`, `MISSING`, `MALFORMED`, `AMBIGUOUS`. A unit in a wave
  that has not been emitted yet is `not started`. Do not soften or reinterpret a state —
  `TRUNCATED` is never written as `done`.
- **What happened / what's left** — one line. For a finished unit, its `summary:`. For an
  unfinished one, what remains and what unblocks it, drawn from the Progress log and
  Blockers of whatever partial report exists.

Under the table, three lines and nothing more:

```
done <x>/<n>   outstanding <y>   blocked/failed <z>
gate: <the gate line from scan, verbatim>
next: <the single next action — "emit wave 2", "re-emit W1-P3", "delete stale report for X">
```

## start

Ingest the context, decompose, layer, and enter master mode.

### Argument

`/hack-master start <goal>`

`<goal>` is free text. If it is a one-liner and the surrounding conversation carries no
detail, ask for the brief before decomposing — a decomposition from a thin goal produces
units whose prompts are also thin.

### Refusal conditions

- `<goal>` missing → ask what the job is.
- The working directory is not writable → refuse, name the path.
- `master.py plan` reports a cycle or a write-target collision → do **not** emit anything;
  report the offending units and fix the manifest first.

### What it does

1. Restate the goal in one line and confirm it before spending anything on decomposition.
2. Decompose for **maximum width**. The operator optimizes for wall-clock: emit as many
   independently-runnable units per wave as the work allows, and keep the wave count as
   low as the real dependencies permit. See [Decomposing for width](#decomposing-for-width)
   — follow it, it is not optional guidance.
3. Run `python3 <skill-dir>/master.py init <cwd>/tmp/master <goal-slug>`; capture the
   printed `run_id`, `run_dir`, `manifest`, `run_log`.
4. Write `units.json` per the manifest schema. Every unit carries `write_targets` and
   `depends_on`, both explicit, empty list when genuinely empty.
5. Run `python3 <skill-dir>/master.py plan <manifest>`. On collision or cycle, fix the
   manifest and re-run — never proceed past a non-zero exit.
6. Fill `RUN.md` — Goal, Constraints and non-goals, and the first Decisions rows covering
   how the work was split and why. Do this before printing anything, while the reasoning
   is still fresh in context.
7. Print the unit table to chat: label, id, wave, deps, write targets, title — plus the
   width line, the critical path, and any NARROW WAVES / REDUNDANT DEP findings from
   `plan`. If `plan` flagged a narrow wave or a redundant dependency, fix the manifest and
   re-plan **before** printing the table; report only what you could not widen and why.
8. Say master mode is active and name the run id. From here the operator may say "next
   wave", "all done", "where are we" in plain words and this skill routes them to `wave`,
   `join`, `status` without re-typing the command.
9. Do **not** emit wave 1 prompts in the same turn. Let the operator read the table first
   and correct the decomposition — a wrong split is cheapest to fix before any slave runs.

## wave

Emit the copy-paste slave prompts for the next open wave.

### Argument

`/hack-master wave <run>`

### Refusal conditions

- `<run>` missing → ask; offer `current` if `start` ran in this session.
- `<run>` is `current` and no run started here → refuse, ask for an explicit `<run-id>`.
- `master.py scan` exits non-zero → refuse to emit. Print its gate line verbatim and stop.
  A wave whose parents are `MISSING`, `MALFORMED`, `blocked`, or `failed` is never emitted
  "with a warning".

### What it does

1. Run `python3 <skill-dir>/master.py scan <run-dir>`. Emit only if the gate line reads
   `GATE: OPEN`.
2. For each unit in that wave, print a heading carrying the label, then one fenced block,
   self-contained, in this shape:

````
### <label> — <unit-id>

```
<label> <unit-id> — <short title, under ~8 words>

You are a slave session for run <run-id>, unit <unit-id> (wave <n>).

Working directory: <absolute cwd>

## Goal
<scope — what to do, 1-3 sentences>

## Context you cannot derive from the repo
- <fact>
- <fact>

## Non-goals — do not touch
- <thing>

## Files you own (no other session is writing these)
- <write target>

## Done when
- <observable criterion>

## Work in parallel inside this unit — this is a requirement, not a suggestion
Wall-clock is the priority. Everything above is yours alone; no other session touches
these files, so go as wide as the work allows. **Parallel is the default and serial is the
exception you must have a reason for.** Batch every independent tool call into one
message, read many files at once, fan out searches, and spawn subagents — one per
independent leg — rather than walking the legs yourself. Cap concurrency at ~8 workers for
network or SSH work; go wider for local reads.

Before starting, list this unit's legs and mark which are genuinely ordered. Run
everything else at once.

Serialize only what is truly ordered (edit-then-test, migrate-then-verify, write-then-read)
or what shares a write target. Never trade correctness for speed: if going parallel would
force sampling, truncation, or a skipped check, do that part serially and say so in your
report. Report per-item results for any fan-out — successes and failures both, never only
the successes.

## Required: your state report — create it FIRST, fill it as you go
Write exactly this file: <the report path printed by scan for this unit>

**Create it before you start working**, with `status: in-progress` and the skeleton below.
Then update it as you go — append to the Progress log as each step completes, add files to
Files changed as you touch them, record findings the moment you learn them. Do not save
everything for the end: if this session is closed or dies, whatever is on disk is the only
thing the master will ever know about your unit, and a report that stops mid-story still
tells it how far you got and what you hit.

---
unit: <unit-id>
label: <label>
wave: <n>
status: in-progress
summary: <one line, updated as the picture changes>
---

# <label> <unit-id> — <short title>

## Progress log
- started: <what you are about to do>

## Files changed
## Findings
## Blockers
## Next-step hint

When you finish, do these two things last, in this order: set `status:` to its final value,
then write the closing line as the final line of the file.

- Finished the work        -> status: done                 -> final line: `Job is done.`
- Could not finish         -> status: blocked | failed     -> final line: `Job stopped: <reason>.`

The closing line is how the master tells a completed report from a session that died
mid-write — a terminal status without it is read as a crash, not as success. Write the
report even if you fail or get blocked; a missing report blocks every dependent unit in
this run.

Finally, print `Job is done.` (or `Job stopped: <reason>.`) as the last line of your reply
in this session, so the operator can see at a glance which tabs are finished.
```
````

3. One fenced block per unit, nothing between them but the labelled heading. Each block
   must stand alone — a slave sees none of this conversation, so a prompt that references
   "the plan above" or "as discussed" is a defect.
4. **The first line of the prompt is the label, the unit id, and a short title — nothing
   else.** No prose, no "You are a slave session" before it. That line becomes the slave
   session's tab title, and it is the only thing distinguishing seven otherwise-identical
   tabs. Emit the blocks in label order, `P1` through `Pn`, so the operator can paste
   straight down the list.
5. After the blocks, print one line: the labels emitted (`W2-P1/7 … W2-P7/7`), that they
   are safe to run concurrently, and to come back with "join" when the reports are in.

### Prompt content rules

- **Carry the reasoning, not just the task.** The slave lacks the argument behind every
  decision. Constraints and non-goals go in the prompt explicitly, or the slave produces
  plausible-but-wrong work.
- **Never reference master context by pointer.** No "see the table", no "same as unit 3".
- **Label first, always.** First line = `<label> <unit-id> — <short title>`. It is the
  operator's place-keeper and the slave's tab title.
- **Report first, filled incrementally, closed by a marker.** Every prompt says to create
  the report before starting, keep it current as the work proceeds, and end with
  `Job is done.` / `Job stopped: <reason>.`. A slave that writes its report only at the end
  leaves nothing behind when it dies, which is the failure this pattern exists to avoid.
- **State the write targets** so the slave knows its lane and stays in it.
- **Always include the parallelism clause.** A slave owns its whole lane and no other
  session writes into it, so intra-unit concurrency is free speed — but it must be spelled
  out or the slave defaults to serial. Carry the quality gate with it: correctness first,
  serialize ordered steps, no thinned verification, per-item reporting on any fan-out.
  When the unit genuinely has only one ordered leg, say that in the clause rather than
  dropping it.
- **Done-criteria are observable** — "tests pass", "the job appears in the pipeline", not
  "it works".

## join

Read the reports and report the truth about them.

### Argument

`/hack-master join <run>`

### Refusal conditions

- `<run>` missing or unresolvable → ask, naming the value.
- Run directory or `units.json` absent → refuse, name the path.

### What it does

1. Read `RUN.md`, then run `python3 <skill-dir>/master.py scan <run-dir>` and print its
   table.
2. Read every report whose status is `done` and fold its Findings and Next-step hint into
   the master's own context — this is the point of the whole pattern. Then append a
   `### wave <n>` block to `RUN.md` under Findings capturing what those reports taught and
   what it changed. Do this **before** reporting to chat; a finding that lives only in the
   transcript is lost when the context compacts.
3. For every `MISSING` or `MALFORMED` unit, say so plainly and name the expected path. Do
   not infer that a unit succeeded because the operator said the slaves are finished.
4. For every `RUNNING` or `TRUNCATED` unit, read the partial report — that is what
   incremental writing bought. Report how far the slave got, what its Progress log shows it
   was doing when it stopped, and whether the remaining work is a re-run of the whole unit
   or a resume from its last logged step. `TRUNCATED` means the session died mid-write; do
   not treat its `status:` line as authoritative.
5. For every `blocked` or `failed` unit, quote its Blockers and propose one of: re-emit
   the same unit, split it, or absorb it into the master.
6. Report whether the decomposition needs revision — a finding from wave N often changes
   what wave N+1 should be. Rewriting `units.json` between waves is expected, not a
   failure; re-run `master.py plan` after any edit and add a Decisions row to `RUN.md`
   saying what changed and why. Labels are re-derived by the script after a re-plan; say
   so explicitly when they shift, so the operator does not paste against stale numbers.
7. State whether the gate is open for the next wave. Do not emit prompts here — `wave`
   does that.
8. **Finish with the [Run ledger](#run-ledger)** — the full unit table, every row, plus the
   three summary lines. This is the last thing `join` prints; nothing follows it. A `join`
   that reports findings but no ledger is incomplete.

## status

Snapshot without side effects.

### Argument

`/hack-master status <run>`

### What it does

Runs `master.py scan`, then prints the [Run ledger](#run-ledger) and stops. No prompt
emission, no manifest edits, and no folding of findings into `RUN.md` — that is `join`'s
job. Reports are opened only far enough to fill the ledger's last column for units that are
not `done`.

## Master mode

`start` turns it on for the rest of the session. While active:

- This session does **no unit work**. If the operator asks the master to do a unit
  directly, say it belongs to unit `<id>` in wave `<n>` and ask whether to absorb it
  (which changes the manifest) or emit it as a slave prompt.
- Plain-language routing is live: "next wave" → `wave current`, "they're all done" →
  `join current`, "where are we" → `status current`.
- Guard the context. The master's value is that it holds what the slaves cannot. Do not
  fill it with unit-level detail that a report already carries.
- Treat `RUN.md` as the source of truth over recollection. A long master session is the
  most likely thing in this pattern to lose fidelity; re-read the run log at the top of
  every sub-op rather than trusting memory of a decision made twenty turns ago.

Master mode ends when the operator says so, or when `scan` reports the run complete.

## Rules

- **Never emit a dependent unit early.** The gate is `master.py scan`, and its exit code
  is the decision, not the model's read of the situation.
- **Never treat a missing report as done.** Absent file, absent status line, and unparsed
  frontmatter are all failures.
- **Never write prompts to a file instead of chat.** The operator copies them from the
  transcript.
- **Never emit a prompt whose first line is not `<label> <unit-id> — <short title>`.**
  That line is the slave session's tab title; prose there makes every tab identical.
- **Never invent or renumber labels.** They come from `master.py`; after a re-plan, say
  which ones moved. Never rename an already-written report to match a new label — `scan`
  finds it by unit id, and a rename risks leaving two files for one unit.
- **Never let a finding live only in the transcript.** It goes into `RUN.md` at `join`,
  before anything is reported to chat.
- **Never spawn a slave.** Emit the prompt; the operator runs it.
- **Maximize width, minimize wave count.** Split whenever write targets are disjoint;
  add a dependency only for a real data dependency. Depth is the cost, width is free.
- **Never emit a prompt without the parallelism clause.** Cross-session waves are only
  half the speedup; the other half is the slave going wide inside its own lane, and it
  will not unless told.
- **Never merge two units to tidy the table.** A merged unit is a serialized unit.
- **Never let two units in one wave share a write target.** `plan` enforces this; do not
  work around it by removing the target from the manifest.
- **Report the whole set on every join** — failures and missing units included, never only
  the successes.
- **Always close `join` and `status` with the run ledger** — every unit, one row each,
  states copied from `scan` unchanged. A partial table, or a state softened into `done`,
  makes the operator believe work exists that does not.
- **Re-plan freely between waves.** The manifest is a working document; the run id and the
  report paths are what must stay stable.
