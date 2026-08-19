---
name: doc-consolidation
description: Shrink an oversized skill or doc file by splitting it into a DAG of paragraph nodes, verifying each via a fresh-context subagent, and trimming anything the tool already teaches — with per-paragraph commit tracking in sqlite. Use when a skill file has grown unmanageably large and you want to shrink it systematically while moving its rules into the tool's help/notes/output.
license: Proprietary. All rights reserved.
compatibility: Requires python3, git, and a pi/Claude-Code harness with fresh-context subagent support.
metadata:
  author: elecnix
  version: "1.0"
  example-run: orchestrate-pi-agents 370KB->231KB (38%)
---

# doc-consolidation

Shrink an oversized skill or doc file without losing its decision-guidance core, by
verifying every paragraph and deleting or trimming whatever the tool already teaches.

## When to use this skill

Use it when a skill or doc file has grown past roughly 10K words and much of it restates
tool behavior — verb tables, help-text, mechanics, incident backstories. Use it when you
want a systematic, tracked shrink rather than a hand edit. Use it when you want to surface
rules that belong **in** the tool (help text, notes, behavior), not in prose.

## The core idea

Split the doc into a **DAG of paragraph nodes**. Assign each node a list of verification
tasks. Execute each node's tasks with **one fresh-context subagent** in its own git worktree
(`context: "fresh"`, never `fork` — a forked worker inherits the orchestrator's scripts and
once ran `apply-node.py` itself, committing to the shared branch past the verification
gate). The subagent verifies accuracy, redundancy, and discoverability, and produces a
diff. The orchestrator applies it (one commit per node) and records the commit id on the
node's tasks in a local sqlite.

**The shrink criterion** (from the skill's own routing test): if the orchestrator would
discover a fact by normal use of the tool — `--help`, output, notes — the doc does not have
to say it. Default to deletion. A paragraph stays only if (a) it is needed before any tool
runs, or (b) it is an illustration no verb can own.

## Quick start

**First: run `--help` on every script, do not read the source.** The scripts are
self-documenting via argparse `--help` — that output is the discoverable summary of
flags, defaults, and derived paths, and it is all you need to drive the pipeline. Reading
the full source wastes context (the five scripts are ~40KB) and is exactly the kind of
restate-the-tool prose this skill exists to cut. Run these first:

```bash
python3 scripts/split-skill-dag.py --help
python3 scripts/make-worktrees.py  --help
python3 scripts/drive-nodes.py     --help
python3 scripts/apply-node.py      --help
python3 scripts/status.py          --help
```

Only read a script's source if `--help` does not answer a specific question.

Every script derives its paths from `--skill` (see `common.py`): the DB and
briefs/diffs/verdicts/proposals land in `~/.cache/<doc-slug>/`, per-node worktrees in
`<wt-root>/<doc-slug>/`, and the doc is committed in its own git repo. The tool script
(the one whose help/output the doc must not restate) is auto-detected, and commit
identity comes from the repo's git config — no flags needed beyond the doc path.

```bash
# 1. Split the doc into a DAG + tasks, write per-node briefs
python3 scripts/split-skill-dag.py --skill PATH/TO/SKILL.md
      # everything else (DB, briefs, worktrees, tool, co-author) derives from --skill
      # --tool PATH / --no-tool override tool detection; --co-author NAME overrides git config

# 2. Create git worktrees for the first wave (5 in parallel)
python3 scripts/make-worktrees.py --nodes 5

# 3. For each schedulable node, spawn ONE fresh-context subagent (context: "fresh",
#    model from --model, default deepseek-v4-flash:low) with its brief. drive-nodes.py
#    prints the exact spawn task:
python3 scripts/drive-nodes.py --next 5

# 4. When the wave completes, apply + commit + record each node:
python3 scripts/apply-node.py <node-id>

# 5. Repeat steps 2-4 until `apply-node.py --next` is empty. Progress tick:
python3 scripts/status.py
```

Each of steps 1-4 is covered in detail in [the reference](references/REFERENCE.md).

## Prompt modules

The prompt is assembled from composable modules, selected with `--modules` on
`split-skill-dag.py` and `drive-nodes.py` (default `trim,progressive-disclosure` = all):

- **trim** — trim text verbatim-restated in a tool's header/`--help`/code-comment to a
  one-line pointer; keep decision rules even if partially discoverable.
- **progressive-disclosure** — for each kept rule about a situation a tool *produces or reports*
  (an invocation, a failure, a status), disclose it at the output where that situation appears —
  `--help` for invocation rules, the failure output for failure-mode rules, the status output for
  status rules — so one run gives the caller the situation AND the rule. If the tool does not
  already print it there, write a proposal (output location + text to add) without editing the script.

A run with **all modules** (the default) does both in one pass. A run with **only
`--modules progressive-disclosure`** skips trimming and only surfaces embeddable rules — useful on
a doc already trimmed in an earlier pass. The base checks (accuracy, consistency,
currency, `script:line` citations, keep-if-cross-script-orchestration) always apply.

The selected modules are recorded in the DB meta table, so `drive-nodes.py` reads them
back without a flag; pass `--modules` to `drive-nodes.py` to override for one run.

The proposals accumulate in `proposals/`. A separate tool-change PR implements them
(edits the tool scripts to embed the rules) and trims the doc to pointers — that is
a code change with its own gates, not a docs-only fast-path. On the PRI-2201 run the
progressive-disclosure module surfaced 23 proposals across 11 nodes where a trim-only prompt had
surfaced 0.

## Deliverables

When the DAG is fully drained you get:

- A **much smaller doc** (worked example cut 38% of words) whose remaining paragraphs were
  each verified accurate and non-redundant.
- A **sqlite tracking DB** linking every node's task to the commit that resolved it.
- A **`proposals/` directory** of `progressive-disclosure` rules that belong in the tool, for a
  separate tool subagent to implement.
- The decision-guidance core — "what you need before any tool runs" — kept intact.

See [the worked example](references/WORKED-EXAMPLE.md) for the orchestrate-pi-agents run.

## Files

| Path | Role |
|------|------|
| `SKILL.md` | This overview. |
| `scripts/` | The pipeline: split, worktrees, drive, apply, status + `common.py` + `prompt_modules.py` + `dedup-pass.py` (cross-node dedup) (composable trim / progressive-disclosure prompt modules). |
| `references/REFERENCE.md` | Full method: DAG, task catalog, subagent definition, wave model, apply flow, DB schema. |
| `references/WORKED-EXAMPLE.md` | The orchestrate-pi-agents run with real numbers. |

## Cross-node dedup pass

The per-node DAG checks duplication **inside** a node (does this paragraph
restate the script?). It cannot see that the same rule is stated in **two
different sections** — e.g. a golden rule repeated in the detailed step, or a
constraint stated in both the intro and a later section. `dedup-pass.py` runs a
**whole-doc** pass that spots rules stated in more than one node, keeps the
canonical statement in one section, and trims the duplicates to one-line
pointers.

```bash
# After a per-node pass has been applied, run a cross-node dedup pass:
python3 scripts/dedup-pass.py --skill PATH/TO/SKILL.md
# It prints a spawn task; the subagent reads the whole doc, finds restatements,
# and produces diffs/dedup.diff + reports/dedup.md. Apply + commit:
git -C <worktree> apply --3way -p0 --whitespace=nowarn <cache>/diffs/dedup.diff
```
