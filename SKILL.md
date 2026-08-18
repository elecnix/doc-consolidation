---
name: doc-consolidation
description: Shrink an oversized skill or doc file by splitting it into a DAG of paragraph nodes, verifying each via forked subagents, and trimming everything the tool already teaches — with per-paragraph commit tracking in sqlite. Use when a skill file has grown unmanageably large and you want to systematically shrink it while embedding its rules into the tool's help/notes/output.
---

# doc-consolidation

## The problem

A skill or doc file grows large over time as lessons are appended. Every paragraph costs
context in every session that loads the skill, including sessions the rule will never apply
to. Much of the prose restates what the tool's `--help` / output / notes already teach.
Shrinking it by hand is error-prone, never finishes, and the same defect gets rediscovered
when an agent copies a stale snippet instead of fixing the tool.

## The method

Split the doc into a **DAG of paragraph nodes** (one per section, with oversized sections
chunked into consecutive sub-nodes). Assign each node a **list of verification tasks**.
Execute each node's tasks via **one forked subagent** in its own git worktree. The subagent
verifies accuracy, redundancy, and discoverability; produces a diff; the orchestrator applies
it (**one commit per node**) and records the commit id on the node's tasks in a local sqlite.

The shrink criterion, per the skill's own routing test: **if the orchestrator would discover
a fact by normal use of the tool (help, output, notes), the doc does not have to say it.**
Default to deletion. A paragraph stays only if (a) it is needed before any tool runs, or
(b) it is an illustration no verb can own.

## The DAG

- **Nodes**: one per section (heading + body); sections over ~500 words are chunked into
  consecutive chunk nodes at blank-line boundaries. Node 0 is the frontmatter.
- **Edges**: heading hierarchy (child depends on ancestor), chunk order (chunk k+1 depends
  on chunk k), and cross-references (a node that names another node's heading depends on it).
- **Scheduling**: a node is schedulable when all its dependencies are done. The frontier
  peaks wide (140+ parallel) and the whole DAG finishes in ~13 waves.
- **Identity**: nodes keyed by content hash; on re-split, unchanged nodes keep their task
  history.

## The task catalog

Every node gets `verify-accuracy` + `verify-redundancy`. Nodes that invoke `fleet <verb>` /
`fleet note <topic>` also get `verify-discoverability`. Nodes with code blocks or tool names
also get `verify-consistency`. Nodes with dates or operator rulings also get `verify-currency`.

| Task | Title | What it checks |
|------|-------|----------------|
| verify-accuracy | Verify this paragraph is still accurate | Claims against the current tool behavior, verb names, defaults, paths |
| verify-redundancy | Verify this paragraph is redundant | Whether the tool's help/output/notes already say this |
| verify-discoverability | Verify the content is discoverable from the tool | Whether normal tool usage would teach the orchestrator this rule |
| verify-consistency | Verify the paragraph agrees with the script | Names, defaults, mechanics against the script source |
| verify-currency | Verify the paragraph is current | Dates, operator rulings, superseded references |

**Verdicts** (one per task): `keep` / `trim-to-pointer` / `delete` / `move-to-fleet`.
Move-to-fleet proposals accumulate in a `proposals/` dir for a separate fleet-tool subagent
(the fleet change involves unit tests, which is why it is delegated).

## The subagent definition

**Agent**: `worker` (builtin), `context: "fork"` — inherits parent context for the goal and
the tool's location.

**Brief** (one file per node, `briefs/N%04d-*.md`): contains the node's full paragraph text,
its task list (id + title + description), the heading path, the worktree path, the fleet
script path, the investigation recipe, the verdict options, and the exact output contract.

**Task text** (passed to the subagent):
> Read the brief at `<brief path>` and execute it exactly. The brief tells you what to
> investigate, what verdicts to produce, and where to write the diff and verdicts files.
> Your worktree is `<wt path>` — edit ONLY that SKILL.md. When done, report: your verdicts,
> one-line reasons, and the diff path. IMPORTANT: do not read the whole fleet script. Run
> `fleet help <verb>` for the verbs the paragraph names, compare, write your verdicts, and
> finish. Aim to complete in under 3 minutes.

The efficiency constraint ("don't read the whole script, finish in 3 min") is critical —
without it, the first node spent 7 minutes re-reading the 920KB script in a loop. With it,
nodes finish in 1-2 minutes.

## The wave model (5 in parallel)

1. `python3 scripts/make-worktrees.py --nodes 5` — create 5 git worktrees, each on a
   `split-node-<id>` branch from the current split-branch HEAD. Each worktree is a full
   checkout so the subagent can run `fleet help` and grep the script.
2. Spawn 5 forked subagents via `runs.all` (one per node), each with its worktree path.
3. When all 5 complete, apply each: `python3 scripts/apply-node.py <id>` — applies the
   diff with `git apply --3way` (handles context drift from sibling trims), runs the
   doc/script agreement checks (fences balanced, verbs resolve, notes exist), commits
   SKILL.md with an explicit path, and records the commit id on the node's tasks.
4. Repeat until the frontier is empty.

**Per-worktree isolation** is the key to conflict-free parallelism: each subagent's diff is
against the current HEAD (not a stale pristine), so `git apply --3way` lands cleanly. Without
it (diffing against a pristine snapshot), adjacent nodes' diffs conflicted when a sibling's
trim changed the context.

## The tooling

All scripts live in `scripts/` and take `--skill`, `--db`, `--fleet` params with sensible
defaults (override for any doc, not just the orchestrate-pi-agents skill).

| Script | Role |
|--------|------|
| `split-skill-dag.py` | Parse the doc → nodes + edges + tasks in sqlite; write briefs. `--force` rebuilds; `--briefs-only` regenerates briefs without touching the DB. |
| `apply-node.py <id>` | Apply one node's diff, commit, record commit id. `--status` / `--next N` for progress. |
| `drive-nodes.py` | List schedulable nodes; print the subagent spawn task. `--next N` / `--status`. |
| `make-worktrees.py` | Create / clean up per-node git worktrees. `--nodes N` / `--cleanup`. |
| `status.py` | One-line progress for the 2-minute monitor tick. |

**The DB** (`~/.cache/<name>/skill-dag.db`): sqlite with `nodes`, `edges`, `tasks`,
`node_tasks` tables. Each task row carries `status`, `commit_id`, `done_at` — so you can
track which paragraph's tasks were done and which commit applied them.

**The monitor**: `monitor` tool with a 2-minute poll of `status.py` — emits one line per
tick: `done X/N | tasks Y/M (+Z/2m) | frontier F`. The `+Z/2m` is the throughput signal.

## The apply + commit + record flow

```
subagent writes:  diffs/N%04d.diff (git diff --no-prefix, empty = keep)
                  verdicts/N%04d.md (one line per task: "T0001 verify-accuracy: keep — reason")
                  proposals/N%04d.md (only if move-to-fleet)

orchestrator:     python3 apply-node.py <id>
                  → git apply --3way (handles context drift)
                  → check_doc_agreement (fences, verbs, notes)
                  → git add SKILL.md && git commit -m "skill-split: node <id> <heading> (<tasks>)"
                  → record commit_id on node's tasks in sqlite
                  → mark node done
```

One commit per node modification. Keep verdicts (no edit) record no commit. The commit id
is recorded on every task of the node, so the DB links each task to the commit that resolved
it.

## Worked example: orchestrate-pi-agents

- **Before**: 370,945 bytes / 5,587 lines / 61,625 words
- **After**: 231,445 bytes / 3,415 lines / 38,482 words (**38% cut**)
- **216 nodes**, 748 tasks, 150 commits, ~1h40m, 37 waves of 5
- **149 nodes trimmed**, 67 kept as-is; 270 trim-to-pointer, 463 keep, 10 delete, 3 move-to-fleet
- **10+ real accuracy drifts caught** (stale reset-cadence output, wrong bucket counts, a
  `--session` flag that doesn't exist, a stale cross-reference, a wrong order-file format)
- **Zero conflicts** after the per-worktree change (one early conflict resolved manually)
- The trimmed file passes `fleet selfcheck` (80 verbs, 26 notes, fences balanced)

## When to use this skill

- A skill file has grown past ~10K words and much of it restates tool behavior.
- You want to shrink it systematically, with per-paragraph verification and commit tracking.
- You want to identify rules that should move INTO the tool (move-to-fleet proposals).
- You do NOT want to lose the decision-guidance core (the "needed before any tool runs" rules).

## What stays in the doc

Per the routing test: how to decide, how to report, what to delegate, and the failure modes
of monitoring itself. Illustrations (BROKEN/FIXED pairs, measured incidents) stay as evidence.
Everything else — recipes, verb tables, mechanics, help-text restatements — trims to a pointer
or deletes.
