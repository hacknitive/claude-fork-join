#!/usr/bin/env python3
"""hack-master worker — run scaffolding, wave layering, and report gating.

Deterministic half of the master/slave orchestration skill. The model decomposes
the goal and writes the manifest; this script owns everything that must not be
guessed: wave layering from the dependency graph, write-target collision
detection, and the hard gate that decides whether the next wave may be emitted.

Sub-commands
    init <root> <goal-slug>   create the run directory, print run id and paths
    plan <manifest>           validate + layer the manifest into waves, in place
    scan <run-dir>            read reports, print status table, gate next wave

No argument has a default. Every required value is a positional or a
required=True flag.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]+$")
UNIT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TERMINAL_STATUSES = ("done", "blocked", "failed")
RUNNING_STATUS = "in-progress"
LEGAL_STATUSES = (RUNNING_STATUS,) + TERMINAL_STATUSES
DONE_MARKER = "job is done."
STOP_MARKER = "job stopped"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


# ----------------------------------------------------------------- init


def cmd_init(args: argparse.Namespace) -> int:
    slug = args.goal_slug
    if not SLUG_RE.match(slug):
        die(f"goal-slug {slug!r} must match ^[a-z0-9][a-z0-9-]*$")

    root = Path(args.root).expanduser().resolve()
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug}"
    run_dir = root / run_id
    if run_dir.exists():
        die(f"run directory already exists: {run_dir}")

    (run_dir / "reports").mkdir(parents=True)
    manifest = run_dir / "units.json"
    manifest.write_text(
        json.dumps({"run_id": run_id, "goal": "", "units": []}, indent=2) + "\n",
        encoding="utf-8",
    )

    # The master session is long-running; its context is the thing most likely to
    # degrade. RUN.md is the durable half — what the master would have to re-derive
    # if its context were lost. The unit table lives in units.json and is NOT
    # duplicated here; two copies drift.
    run_log = run_dir / "RUN.md"
    run_log.write_text(
        "\n".join(
            [
                f"# run {run_id}",
                "",
                "## Goal",
                "<one-line restatement, confirmed with the operator>",
                "",
                "## Constraints and non-goals",
                "- <constraint the whole run must respect>",
                "",
                "## Decisions",
                "| when | decision | why |",
                "|---|---|---|",
                "",
                "## Findings by wave",
                "<folded in at each join — what the reports taught that the master",
                "could not have predicted, and what it changed>",
                "",
                "## Open questions",
                "- <unresolved, and who resolves it>",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"run_id      {run_id}")
    print(f"run_dir     {run_dir}")
    print(f"manifest    {manifest}")
    print(f"run_log     {run_log}")
    print(f"reports_dir {run_dir / 'reports'}")
    return 0


# ----------------------------------------------------------------- plan


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        die(f"manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"manifest is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("units"), list):
        die("manifest must be an object with a 'units' array")
    return data


def validate_units(units: list) -> dict:
    """Return {unit_id: unit}. Dies on any structural problem."""
    by_id: dict = {}
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            die(f"units[{index}] is not an object")
        uid = unit.get("id")
        if not isinstance(uid, str) or not UNIT_ID_RE.match(uid):
            die(f"units[{index}].id {uid!r} must match ^[a-z0-9][a-z0-9-]*$")
        if uid in by_id:
            die(f"duplicate unit id: {uid}")
        if not unit.get("title"):
            die(f"unit {uid} has no title")
        for field in ("depends_on", "write_targets"):
            value = unit.get(field, [])
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                die(f"unit {uid}.{field} must be a list of strings")
            unit[field] = value
        by_id[uid] = unit

    for uid, unit in by_id.items():
        for dep in unit["depends_on"]:
            if dep not in by_id:
                die(f"unit {uid} depends on unknown unit {dep!r}")
            if dep == uid:
                die(f"unit {uid} depends on itself")
    return by_id


def layer_waves(by_id: dict) -> list:
    """Kahn layering. Wave N holds every unit whose deps all sit in waves < N."""
    remaining = dict(by_id)
    placed: dict = {}
    waves: list = []
    while remaining:
        ready = [
            uid
            for uid, unit in remaining.items()
            if all(dep in placed for dep in unit["depends_on"])
        ]
        if not ready:
            die(
                "dependency cycle among units: "
                + ", ".join(sorted(remaining))
                + " — no unit has all dependencies satisfied"
            )
        ready.sort()
        wave_no = len(waves) + 1
        for uid in ready:
            remaining[uid]["wave"] = wave_no
            placed[uid] = remaining.pop(uid)
        waves.append(ready)
    return waves


def wave_labels(by_id: dict) -> dict:
    """Stable copy-paste labels: W<wave>-P<index>/<units in wave>.

    Derived from the manifest, never minted per emission, so the same unit carries
    the same label every time it is printed — the operator's place-keeper across a
    long paste session and the leading token of each slave session's tab title.
    """
    waves = defaultdict(list)
    for uid, unit in by_id.items():
        waves[unit["wave"]].append(uid)
    labels = {}
    for wave_no, uids in waves.items():
        uids.sort()
        for index, uid in enumerate(uids, start=1):
            labels[uid] = f"W{wave_no}-P{index}/{len(uids)}"
    return labels


LABEL_STEM_RE = re.compile(r"W\d+-P\d+")


def label_stem(label: str) -> str:
    """`W1-P3/7` -> `W1-P3`. The `/count` is display-only; a filename cannot hold it."""
    return label.split("/", 1)[0]


def report_name(label: str, uid: str) -> str:
    return f"{label_stem(label)}-{uid}.md"


def critical_path(by_id: dict) -> list:
    """Longest dependency chain — the run's depth floor. Attack this to go faster."""
    memo: dict = {}

    def chain(uid: str) -> list:
        if uid in memo:
            return memo[uid]
        deps = by_id[uid]["depends_on"]
        best = max((chain(d) for d in deps), key=len, default=[])
        memo[uid] = best + [uid]
        return memo[uid]

    return max((chain(uid) for uid in by_id), key=len, default=[])


def redundant_deps(by_id: dict) -> list:
    """Dependencies already implied transitively — noise that hides the real graph."""
    reach: dict = {}

    def ancestors(uid: str) -> set:
        if uid in reach:
            return reach[uid]
        reach[uid] = set()
        acc: set = set()
        for dep in by_id[uid]["depends_on"]:
            acc.add(dep)
            acc |= ancestors(dep)
        reach[uid] = acc
        return acc

    findings = []
    for uid, unit in by_id.items():
        direct = unit["depends_on"]
        for dep in direct:
            indirect = set()
            for other in direct:
                if other != dep:
                    indirect |= ancestors(other)
            if dep in indirect:
                findings.append(f"{uid} depends on {dep}, already implied transitively")
    return findings


def check_collisions(waves: list, by_id: dict) -> list:
    """Two units in the same wave writing the same target is a bug, not a race."""
    problems = []
    for wave_no, uids in enumerate(waves, start=1):
        owners = defaultdict(list)
        for uid in uids:
            for target in by_id[uid]["write_targets"]:
                owners[target].append(uid)
        for target, holders in sorted(owners.items()):
            if len(holders) > 1:
                problems.append(f"wave {wave_no}: {target} written by {', '.join(holders)}")
    return problems


def cmd_plan(args: argparse.Namespace) -> int:
    path = Path(args.manifest).expanduser().resolve()
    data = load_manifest(path)
    by_id = validate_units(data["units"])
    if not by_id:
        die("manifest has no units")

    waves = layer_waves(by_id)
    collisions = check_collisions(waves, by_id)

    data["units"] = [by_id[uid] for wave in waves for uid in wave]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    widths = [len(w) for w in waves]
    path = critical_path(by_id)
    print(
        f"units {len(by_id)}   waves {len(waves)}   "
        f"max width {max(widths)}   avg width {sum(widths) / len(waves):.1f}"
    )
    labels = wave_labels(by_id)
    for wave_no, uids in enumerate(waves, start=1):
        print(f"\nwave {wave_no}  ({len(uids)} parallel)")
        for uid in uids:
            deps = ", ".join(by_id[uid]["depends_on"]) or "-"
            print(f"  {labels[uid]:<10} {uid:<24} deps: {deps:<28} {by_id[uid]['title']}")

    # Depth is the cost: every wave boundary is a serial round-trip through the
    # operator. Width is free. Surface what forces the depth so it can be attacked.
    print(f"\ncritical path ({len(path)} deep)  {' -> '.join(path)}")
    narrow = [str(i) for i, w in enumerate(widths, start=1) if w == 1 and len(waves) > 1]
    if narrow:
        print(
            f"NARROW WAVES: {', '.join(narrow)} hold a single unit — each costs a full "
            "round-trip for no parallelism. Split the unit, or drop a dependency that is "
            "not a real data dependency, and re-plan."
        )
    for line in redundant_deps(by_id):
        print(f"REDUNDANT DEP: {line}")

    if collisions:
        print("\nWRITE-TARGET COLLISIONS — these units cannot run in parallel:")
        for line in collisions:
            print(f"  {line}")
        print("Split the units or move one to a later wave, then re-run plan.")
        return 1

    print("\nno write-target collisions")
    return 0


# ----------------------------------------------------------------- scan


def find_report(reports_dir: Path, uid: str, label: str) -> list:
    """Locate a unit's report.

    The canonical name is `<label>-<unit-id>.md`, but labels move whenever the
    manifest is re-planned, so a report written under an older label must still be
    found. Match on the unit id and accept any label prefix; return every candidate
    so an ambiguous pair (stale label + current label) can be surfaced rather than
    silently resolved to the wrong one.
    """
    exact = reports_dir / report_name(label, uid)
    if exact.is_file():
        return [exact]
    # The prefix must be a label stem (`W2-P7`) and nothing else. Matching any text
    # that merely ENDS in `-<uid>` makes every unit id that is a suffix of another
    # collide: `normalize-analytics` would claim `W3-P2-merge-normalize-analytics.md`
    # and both units then read AMBIGUOUS.
    candidates = sorted(
        p
        for p in reports_dir.glob(f"*{uid}.md")
        if p.stem == uid
        or (
            p.stem.endswith(f"-{uid}")
            and LABEL_STEM_RE.fullmatch(p.stem[: -(len(uid) + 1)])
        )
    )
    return candidates


def read_report(path: Path) -> dict:
    """Parse the report frontmatter. Absent terminal status is not success.

    Slaves write the report incrementally — `status: in-progress` from the moment
    work starts, updated as it goes — so a session that dies still leaves the story
    behind. That makes a completed file indistinguishable from a truncated one on
    frontmatter alone: a crash after the header leaves `status: done` sitting above
    a half-written body. The closing marker line is what tells them apart, so a
    terminal status without it is TRUNCATED, not success.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    fields: dict = {}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()

    status = fields.get("status", "").lower()
    summary = fields.get("summary", "")
    if status not in LEGAL_STATUSES:
        return {"status": "MALFORMED", "detail": f"status={status or '<absent>'}"}

    if status == RUNNING_STATUS:
        tail = next((ln.strip() for ln in reversed(lines) if ln.strip()), "")
        return {"status": "RUNNING", "detail": f"{summary}  [last line: {tail[:40]}]"}

    closing = next((ln.strip().lower() for ln in reversed(lines) if ln.strip()), "")
    if not (closing.startswith(DONE_MARKER) or closing.startswith(STOP_MARKER)):
        return {
            "status": "TRUNCATED",
            "detail": f"status={status} but no closing marker — session died mid-write",
        }
    return {"status": status, "detail": summary}


def cmd_scan(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest_path = run_dir / "units.json"
    data = load_manifest(manifest_path)
    by_id = validate_units(data["units"])
    if not by_id:
        die("manifest has no units")
    if any("wave" not in unit for unit in by_id.values()):
        die("manifest not layered — run `plan` before `scan`")

    labels = wave_labels(by_id)
    reports_dir = run_dir / "reports"
    states: dict = {}
    for uid, unit in by_id.items():
        found = find_report(reports_dir, uid, labels[uid])
        if not found:
            states[uid] = {
                "status": "MISSING",
                "detail": str(reports_dir / report_name(labels[uid], uid)),
            }
        elif len(found) > 1:
            states[uid] = {
                "status": "AMBIGUOUS",
                "detail": "multiple reports: " + ", ".join(p.name for p in found),
            }
        else:
            states[uid] = read_report(found[0])
            if found[0].name != report_name(labels[uid], uid):
                states[uid]["detail"] += f"  [filed as {found[0].name}]"

    waves = defaultdict(list)
    for uid, unit in by_id.items():
        waves[unit["wave"]].append(uid)
    print(f"run {data.get('run_id', run_dir.name)}   units {len(by_id)}   waves {len(waves)}")
    for wave_no in sorted(waves):
        print(f"\nwave {wave_no}")
        for uid in sorted(waves[wave_no]):
            state = states[uid]
            print(
                f"  {state['status']:<10} {labels[uid]:<10} {uid:<24} {state['detail'][:60]}"
            )

    # Gate: the next wave is the lowest wave not fully done. It may only be
    # emitted when every earlier wave is done — MISSING and MALFORMED both block.
    next_wave = None
    for wave_no in sorted(waves):
        if not all(states[uid]["status"] == "done" for uid in waves[wave_no]):
            next_wave = wave_no
            break

    if next_wave is None:
        print("\nGATE: all waves done — run complete")
        return 0

    blockers = [
        (uid, states[uid]["status"])
        for wave_no in sorted(waves)
        if wave_no < next_wave
        for uid in waves[wave_no]
        if states[uid]["status"] != "done"
    ]
    incomplete = [uid for uid in sorted(waves[next_wave]) if states[uid]["status"] != "done"]

    if blockers:
        print("\nGATE: BLOCKED — earlier wave not clean:")
        for uid, status in blockers:
            print(f"  {uid}: {status}")
        return 1

    running = [uid for uid in waves[next_wave] if states[uid]["status"] == "RUNNING"]
    if running or (
        any(states[uid]["status"] == "MISSING" for uid in waves[next_wave])
        and any(states[uid]["status"] != "MISSING" for uid in waves[next_wave])
    ):
        print(f"\nGATE: wave {next_wave} IN FLIGHT — {len(incomplete)} unit(s) outstanding")
        for uid in running:
            print(f"  still running: {uid}  {states[uid]['detail']}")
        return 1

    ambiguous = [uid for uid in sorted(by_id) if states[uid]["status"] == "AMBIGUOUS"]
    if ambiguous:
        print("\nGATE: BLOCKED — a unit has more than one report file:")
        for uid in ambiguous:
            print(f"  {uid}: {states[uid]['detail']}")
        print("Delete the stale one — the master cannot tell which is current.")
        return 1

    print(f"\nGATE: OPEN — emit wave {next_wave} ({len(incomplete)} unit(s))")
    for uid in incomplete:
        print(f"  {labels[uid]:<10} {uid}  {by_id[uid]['title']}")
        print(f"             report -> {reports_dir / report_name(labels[uid], uid)}")
    return 0


# ----------------------------------------------------------------- cli


def main() -> int:
    parser = argparse.ArgumentParser(prog="master.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create a run directory")
    p_init.add_argument("root", help="report root, e.g. <cwd>/tmp/master")
    p_init.add_argument("goal_slug", help="kebab-case slug for the run")
    p_init.set_defaults(func=cmd_init)

    p_plan = sub.add_parser("plan", help="validate and layer a manifest into waves")
    p_plan.add_argument("manifest", help="path to units.json")
    p_plan.set_defaults(func=cmd_plan)

    p_scan = sub.add_parser("scan", help="read reports and gate the next wave")
    p_scan.add_argument("run_dir", help="path to the run directory")
    p_scan.set_defaults(func=cmd_scan)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
