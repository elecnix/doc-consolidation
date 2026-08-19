# Reference: the doc-consolidation method

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

Every node gets `verify-accuracy` + `verify-redundancy`. Nodes that invoke the tool
(`<tool> <verb>` / `<tool> note <topic>`) also get `verify-discoverability`. Nodes with code
blocks or tool names also get `verify-consistency`. Nodes with dates or operator rulings
also get `verify-currency`.

| Task | Title | What it checks |
|------|-------|----------------|
| verify-accuracy | Verify this paragraph is still accurate | Claims against the current tool behavior, verb names, defaults, paths |
| verify-redundancy | Verify this paragraph is redundant | Whether the tool's help/output/notes already say this |
| verify-discoverability | Verify the content is discoverable from the tool | Whether normal tool usage would teach the orchestrator this rule |
| verify-consistency | Verify the paragraph agrees with the script | Names, defaults, mechanics against the script source |
| verify-currency | Verify the paragraph is current | Dates, operator rulings, superseded references |

**Verdicts** (one per task): `keep` / `trim-to-pointer` / `delete` / `move-to-tool`.
Move-to-tool proposals accumulate in a `proposals/` dir for a separate tool subagent
(the tool change involves unit tests, which is why it is delegated).

## The subagent definition

**Agent**: `worker` (builtin), `context: "fresh"` — a worker MUST use fresh
context, never `fork`. Forked context inherits the orchestrator's session, which includes
the pipeline scripts (`apply-node.py`, `make-worktrees.py`), the sqlite DB, and the apply/
commit flow. A forked worker can (and on a real run, did) run `apply-node.py` itself and
commit to the shared split branch, bypassing the orchestrator's verification step — the
one safety gate the method rests on. Fresh context isolates the worker to its brief, the
worktree, and the tool it is verifying. The recipe is **fresh context + the cheapest
model that still cites its evidence** — `drive-nodes.py --model` defaults to
`deepseek-v4-flash`, which finished nodes in 1-2 minutes and produced accurate,
line-cited verdicts on a 63K-char doc. Per-child thinking is controlled by a **model-string
suffix**, not a `thinking:` field on the item — a bare `thinking: "low"` is silently
ignored (the executor never reads it; the `worker` frontmatter default of `high` wins).
`drive-nodes.py` defaults to `deepseek-v4-flash:low`: the `:low` suffix is parsed by
`resolveEffectiveThinking()` and sets low thinking for that child only. Drop the `:low`
suffix to restore the agent default if verdict quality drops on a harder doc.

**Brief** (one file per node, `briefs/N%04d-*.md`): contains the node's full paragraph text,
its task list (id + title + description), the heading path, the worktree path, the tool
script path, the investigation recipe, the verdict options, and the exact output contract.

**Task text** (passed to the subagent):

> Read the brief at `<brief path>` and execute it exactly. The brief gives you the
> paragraph, the task list, the verdict options, and the output contract. Apply the brief's
> **strict trimming policy**: only trim text VERBATIM restated in the tool's header, a code
> comment, or `--help`/note output; keep every decision rule, failure-mode illustration, and
> `never`/`always` rule even if partially discoverable. Your worktree is `<wt path>` — edit
> ONLY that doc. When done, report your verdicts, one-line reasons each, and the diff path.
> Every verdict line MUST cite the specific `script:line` you verified each claim against
> (e.g. `run-pr-gates.sh:388-389`) — the orchestrator writes those lines into the commit
> body, and a citation is what makes the trim auditable. IMPORTANT: do not read the whole
> tool script. Run `<tool> help <verb>` for the verbs the paragraph names, compare, write
> your verdicts, and finish. Aim to complete in under 3 minutes.

The efficiency constraint ("don't read the whole script, finish in 3 min") is critical —
without it, the first node spent 7 minutes re-reading the 920KB script in a loop. With it,
nodes finish in 1-2 minutes.

## The wave model (5 in parallel)

1. `python3 scripts/make-worktrees.py --nodes 5` — create 5 git worktrees under
   `<wt-root>/<doc-slug>/`, each on a `split-node-<id>` branch from the doc repo's current
   HEAD. Each worktree is a full checkout so the subagent can run `<tool> help` and grep
   the script.
2. Spawn 5 **fresh-context** subagents via `runs.all` (one per node, `context: "fresh"`,
   model from `drive-nodes.py --model`), each with its worktree path. Never use
   `context: "fork"` for workers — see the subagent definition above.
3. When all 5 complete, apply each: `python3 scripts/apply-node.py <id>` — applies the
   diff with `git apply --3way` (handles context drift from sibling trims), runs the
   doc/tool agreement checks (fences balanced, verbs resolve, notes exist), commits the
   doc with an explicit path, and records the commit id on the node's tasks.
4. Repeat until the frontier is empty.

**Per-worktree isolation** is the key to conflict-free parallelism: each subagent's diff is
against the current HEAD (not a stale pristine), so `git apply --3way` lands cleanly.
Without it (diffing against a pristine snapshot), adjacent nodes' diffs conflicted when a
sibling's trim changed the context.

## The tooling

All scripts live in `scripts/` and share one `--skill` flag: everything else derives from
it (see `common.py`). Point `--skill` at any markdown doc in a git repo and the pipeline
works unchanged:

- **DB + artifacts** land in `~/.cache/<doc-slug>/` (skill-dag.db, briefs/, diffs/,
  verdicts/, proposals/, SKILL.orig.md) — a doc slug prefixes the repo dir for generic
  names (SKILL.md, README.md) so two skill repos never collide.
- **Per-node worktrees** live under `<wt-root>/<doc-slug>/N%04d` (default wt-root:
  `~/Source/doc-split-wt`), branched from the doc repo's current HEAD.
- **The tool** (the script whose behavior the doc must not restate) is auto-detected:
  any executable in `<repo>/scripts/` (first one, alphabetically), then any in
  `~/.agents/skills/<doc-name>/scripts/`. `--tool PATH` forces it; `--no-tool` disables
  tool-dependent checks and task kinds (use it when the doc's tool is a *directory* of
  scripts, e.g. `tools/ci/`, that `--tool` cannot name as one file).
- **Commit identity**: the commit trailer uses git config user.name/email of the doc
  repo; `--co-author NAME` overrides.
- **DB meta remembers** the source doc, tool, and co-author, so the apply/drive/status
  scripts need no flags once `split-skill-dag.py` has run — `apply-node.py <id>` just
  works.

| Script | Role |
|--------|------|
| `split-skill-dag.py` | Parse the doc → nodes + edges + tasks in sqlite; write briefs. `--force` rebuilds; `--briefs-only` regenerates briefs without touching the DB. |
| `apply-node.py <id>` | Apply one node's diff, commit, record commit id. `--status` / `--next N` / `--dry-run`. |
| `drive-nodes.py` | Print the subagent spawn task (with model) per node. `--next N` / `--status` / `--model`. |
| `make-worktrees.py` | Create / clean up per-node git worktrees. `--nodes N` / `--node N` / `--cleanup` (also drops leftover branches) / `--list`. |
| `status.py` | One-line progress for the 2-minute monitor tick. |

**The DB** (`~/.cache/<doc-slug>/skill-dag.db`): sqlite with `nodes`, `edges`, `tasks`,
`meta` tables. Each task row carries `status`, `commit_id`, `done_at` — so you can track
which paragraph's tasks were done and which commit applied them. The `meta` table records
the source doc path/commit, the tool path, and the co-author at split time.

**The monitor**: `monitor` tool with a 2-minute poll of `status.py` — emits one line per
tick: `done X/N | tasks Y/M (+Z/2m) | frontier F`. The `+Z/2m` is the throughput signal.

## The apply + commit + record flow

```
subagent writes:  diffs/N%04d.diff (git diff --no-prefix, empty = keep)
                  verdicts/N%04d.md (one line per task: "T0001 verify-accuracy: keep — reason")
                  proposals/N%04d.md (only if move-to-tool)

orchestrator:     python3 apply-node.py <id>
                  → git apply --3way (handles context drift; patch -p0 without a repo)
                  → check_doc_agreement (fences; tool verbs; tool note topics)
                  → git add <doc> && git commit -m "skill-split: node <id> <heading> (<tasks>)"
                  → record commit_id on node's tasks in sqlite
                  → mark node done
```

One commit per node modification. Keep verdicts (no edit) record no commit. The commit id
is recorded on every task of the node, so the DB links each task to the commit that resolved
it.

## What stays in the doc

Per the routing test: how to decide, how to report, what to delegate, and the failure modes
of monitoring itself. Illustrations (BROKEN/FIXED pairs, measured incidents) stay as
evidence. Everything else — recipes, verb tables, mechanics, help-text restatements — trims
to a pointer or deletes.
