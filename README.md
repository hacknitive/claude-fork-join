# claude-fork-join

A Claude Code skill that turns one session into a **fork/join orchestrator**: it holds the
context, decomposes a large job into units of work, layers those units into waves that are
safe to run in parallel, and prints copy-paste prompts for separate Claude Code sessions.
Each of those sessions writes a state report file back to disk; the orchestrator reads the
reports and gates the next wave on them.

Not affiliated with or endorsed by Anthropic.

## Model

- **The orchestrator emits prompts. It does not spawn agents.** No subagents, no background
  tasks. You open each session yourself, so you keep per-session isolation, inspection, and
  cost control.
- **The only channel between sessions is a state report file** on disk, at a path the
  orchestrator assigns. The sessions share no memory.
- **The gate is hard.** A missing report is not success. A report without a terminal
  `status:` line is not success. Either one blocks the next wave.
- **Width is free, depth costs.** Every wave boundary is a serial round-trip through you,
  so the decomposition splits aggressively and depends sparingly.

## Install

Copy into your Claude Code config directory (`~/.claude`, or whatever `CLAUDE_CONFIG_DIR`
points at):

```bash
git clone https://github.com/hacknitive/claude-fork-join.git
cd claude-fork-join
cp -r skills/hack-master   ~/.claude/skills/
cp    commands/hack-master.md ~/.claude/commands/
```

Restart Claude Code. `/hack-master` is then available in every session.

## Usage

```
/hack-master start  <goal>   # decompose into units, layer into waves, create the run
/hack-master wave   <run>    # print the prompts for the next open wave
/hack-master join   <run>    # read every report, verify terminal status, gate the next wave
/hack-master status <run>    # snapshot: units, waves, report states, current gate
```

`<run>` is `current` (the run started in this session) or an explicit run id
(`YYYYMMDD-HHMMSS-goal-slug`).

Arguments are positional and have **no defaults** — a missing or unrecognized slot makes the
skill stop and ask rather than guess.

### Loop

1. `/hack-master start "<your goal>"` — review the unit table and wave layering it proposes.
2. `/hack-master wave current` — paste each printed prompt into its own Claude Code session.
3. Wait for those sessions to finish. Each writes its own report file.
4. `/hack-master join current` — done / missing / blocked / failed.
5. Repeat from step 2 until every wave is closed.

## Run layout

Rooted at `tmp/master/` inside the session's working directory:

```
<cwd>/tmp/master/<run-id>/
├── units.json          # the manifest
├── RUN.md              # the orchestrator's durable memory
└── reports/
    ├── W1-P1-<unit-id>.md
    ├── W1-P2-<unit-id>.md
    └── …
```

Report filenames are label-first so the directory listing sorts by wave, then by prompt.

## Layout of this repo

| Path | Role |
|---|---|
| `skills/hack-master/SKILL.md` | Decomposition rules, prompt template, report schema, mode persistence. |
| `skills/hack-master/master.py` | Deterministic half — run scaffolding, wave layering, write-target collision detection, report gating. |
| `commands/hack-master.md` | The `/hack-master` slash command that routes into the skill. |

The model owns judgment (what the units are, what each session needs to know). The script
owns what must not be guessed (wave order, collisions, whether the gate opens).

## Naming

The skill and slash command are still called `hack-master` — that is the invocation name in
Claude Code. The repository is named for the pattern it implements.

## License

MIT — see [LICENSE](LICENSE).
