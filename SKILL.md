---
name: doc-consolidation
description: shrink an oversized skill or doc file by splitting it into a DAG of paragraph nodes and verifying each node with a fresh-context subagent. Whatever the tool already states gets trimmed, with per-paragraph commit tracking in sqlite. Use when a skill file has grown unmanageably large and you want to shrink it systematically while moving its rules into the tool's help/notes/output.
license: Proprietary. All rights reserved.
compatibility: Requires python3, git, and a pi/Claude-Code harness with fresh-context subagent support. The deslop module also needs vale plus the ai-tells packages (install command under "De-slopping").
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
tool behavior: verb tables, help-text, mechanics, incident backstories. Use it when you
want a systematic, tracked shrink rather than a hand edit. Use it when you want to surface
rules that belong **in** the tool (help text, notes, behavior), not in prose. Use it when you
want to **close the loop**: surface those rules in the tool's output and shrink the doc to point at them (see "The full loop").

A doc can be the right size and still read as machine-written. `--modules deslop` runs on
that case, and it rewords rather than shrinks.

## The full loop (don't stop at proposals)

Shrinking the doc is only the first movement. A rule that belongs in the tool is not done
when the proposal is written. The loop closes in three moves:

1. **Propose** (this pipeline): verify the restated paragraph, write the progressive-disclosure
   proposal, trim the doc to a one-line pointer.
2. **Apply the tool change**: a tool-change PR edits the script to disclose the rule at the output where
   the caller reads it, and (ideally) re-trims the doc to remove the pointer's now-redundant
   copies. Drive one subagent per proposal; batch the trivial ones, delegate the hard ones
   (see "Applying proposals", below).
3. **Re-verify the doc** once the tool change is merged: now that the rule is in the tool's output, the
   pointer's "see the tool" becomes literally true. Confirm the doc copy actually shrinks,
   or the skill grows a duplicate.

The doc and the tool move together. If you make the tool change without trimming the doc (or
the reverse), each half claims the other defines the rule. The result is a doc and a tool that
disagree about which one discloses it.

## The core idea

Split the doc into a **DAG of paragraph nodes**. Assign each node a list of verification
tasks. Execute each node's tasks with **one fresh-context subagent** in its own git worktree
(`context: "fresh"`, never `fork`. A forked worker inherits the orchestrator's scripts and
once ran `apply-node.py` itself, committing to the shared branch past the verification
gate). The subagent verifies accuracy, redundancy, and discoverability, and produces a
diff. The orchestrator applies it (one commit per node) and records the commit id on the
node's tasks in a local sqlite.

**The shrink criterion** (from the skill's own routing test): if the orchestrator would
discover a fact by normal use of the tool (`--help`, output, notes), the doc does not have
to say it. Default to deletion. Keep a paragraph only if (a) the work needs it before any tool
runs, or (b) no tool verb reproduces the illustration. This is the smaller motivation. This
DAG only pays off when you take it through **the full loop above**.

## Quick start

**First: run `--help` on every script, do not read the source.** The scripts are
self-documenting via argparse `--help`. That output is the discoverable summary of
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
briefs/diffs/verdicts/proposals go to `~/.cache/<doc-slug>/`, per-node worktrees in
`<wt-root>/<doc-slug>/`, and the doc is committed in its own git repo. The tool script
(the one whose help/output the doc must not restate) is auto-detected. Commit
identity comes from the repo's git config. You pass no flags beyond the doc path.

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
`split-skill-dag.py` and `drive-nodes.py` (default `trim,progressive-disclosure`; `deslop`
is opt-in):

- **trim**: trim text verbatim-restated in a tool's header/`--help`/code-comment to a
  one-line pointer. Keep decision rules even if partially discoverable.
- **progressive-disclosure**: for each kept rule about a situation a tool *produces or reports*
  (an invocation, a failure, a status), disclose it at the output where that situation appears
  (`--help` for invocation rules, the failure output for failure-mode rules, the status output for
  status rules), so one run gives the caller the situation AND the rule. If the tool does not
  already print it there, write a proposal (output location + text to add) without editing the script.
- **deslop**: rewrite prose that a vale `ai-tells` alert flags as machine-written, bounded
  to the node's own line range. Judge each alert yourself, and let the doc repo's own
  writing standard settle any conflict. Verdicts: `deslop`, `slop_refused`. See
  "De-slopping" for the vale setup.

A run with `trim,progressive-disclosure` (the default) does both in one pass. A run with **only
`--modules progressive-disclosure`** skips trimming and only extracts embeddable rules, useful on
a doc already trimmed in an earlier pass. Every brief demands accuracy and consistency,
whatever the modules. The module set picks the task kinds. `trim` and
`progressive-disclosure` give each node `verify-accuracy` + `verify-redundancy`, and
`deslop` gives it `deslop-prose`. A deslop-only run never gets a trim task, whose text
orders the worker to shrink the paragraph and propose deletion. Dates and operator rulings
in the node add `verify-currency`. A code block adds `verify-consistency` either way, and a
tool name adds it only where the doc has a tool. On a tool-free doc that task checks the
paragraph against the file that defines each name it uses, and against the rest of the doc. The split demands a `script:line` citation
only when the doc has a tool script to cite, and the trim module now holds
keep-if-cross-script-orchestration.

The DB meta table records the selected modules and the style docs, so `drive-nodes.py`
and `apply-node.py` read them back without a flag. Pass `--modules` to `drive-nodes.py` to
override for one run.

The proposals accumulate in `proposals/`. A separate tool-change PR applies them
(edits the tool scripts to embed the rules) and trims the doc to pointers. That is
a code change with its own gates, unlike a docs-only fast path. On the PRI-2201 run the
progressive-disclosure module surfaced 23 proposals across 11 nodes where a trim-only prompt had
surfaced 0.

## De-slopping (`--modules deslop`)

`--modules deslop` fixes the wording and leaves the facts alone. Each node's brief carries
the vale `ai-tells` alerts that fall inside that node's line range, and the worker returns
prose a human would have written. Combine it as `--modules trim,deslop` to trim and reword
in the same pass.

It needs two things the other modules do not: vale, and a named writing standard.

```bash
# one-time setup, since vale is not part of the base requirements
brew install vale
mkdir -p ~/Library/Application\ Support/vale
cat > ~/Library/Application\ Support/vale/.vale.ini <<'INI'
StylesPath = styles
MinAlertLevel = suggestion

Packages = https://github.com/tbhb/vale-ai-tells/releases/download/v1.34.0/ai-tells.zip, \
  https://github.com/tbhb/vale-ai-tells/releases/download/v1.34.0/ai-tells-experimental.zip

[*.{md,mdx,txt}]
BasedOnStyles = ai-tells, ai-tells-experimental
INI
vale sync
```

That config is global, so vale runs from any directory with no per-repo `.vale.ini`. The
split runs it for you; run it by hand only to look:

```bash
vale --output=JSON PATH/TO/SKILL.md            # one file: never pass --glob with a path
vale --glob='*.md' --output=JSON PATH/TO/DIR   # a directory walk: --glob filters that walk
```

`--glob` is a directory-walk filter. Pass it alongside an explicit file path and it filters
that path out: vale prints `{}` and exits 0, the same output as a clean file. Both files in a
comparison must end in `.md`; the same bytes measured 0, 68, or 110 alerts across the
filename and flag combinations. Vale also exits non-zero when it finds alerts, which is not
an error, so capture the output before you test the status.

The module's rules:

- **Answer each alert, do not obey it.** Judge each alert against the sentence in front of
  you, and aim at prose a human would have written. Removing the alert is not the goal, and
  neither is keeping the text.
- **A doc's own habits are not its project voice.** A doc that agents wrote shows the tells
  of the model that wrote it. Frequency is not endorsement: the commonest construction in an
  agent-authored doc is the strongest slop signal in it, so "the rest of the doc does this"
  argues FOR acting. Only the style doc defines the project voice.
- **Act on these rule families by default**: they are machine-writing tells rather than anyone's
  style: `EmDashUsage`, `SemicolonUsage`, `ColonUsage`, `Figurative*`,
  `AnthropomorphicCognition`, `SentenceStartRepetition`, `ContrastiveFormulas`,
  `CataphoricForecasting`, `PassiveVoice`, `VerbTricolon`. On this repo 46 of 120 alerts are
  `EmDashUsage`; on PrizmalSwitch's `AGENTS.md` it is 94 of 224, and `CLAUDE.md` is a symlink
  to that same file, so counting the pair doubles one file. Refuse one only where you can say
  what the construction contributes.
- **One standing refusal: a named term.** The reason is grep, not taste. `NounString` on
  `the switch key owner id` asks you to break every search for the name. Same for `BareHolds`
  where the subject is a named thing. These go on one aggregate line.
- **The doc repo's own writing standard comes first.** Name it with `--style-doc PATH`,
  once per standard, or let the split fall back to the doc repo's `AGENTS.md` / `CLAUDE.md`,
  deduped by realpath because `CLAUDE.md` is usually a symlink to `AGENTS.md`. With neither,
  the split refuses to run this module; `--allow-no-style-doc` overrides that, and every
  brief then tells its worker to refuse each punctuation, phrasing and naming alert rather
  than guess.
  Never reword a named term to satisfy a rule. Rewrite `the switch key owner id` as a
  phrase to clear a `NounString` alert and you destroy the grep.
- **Never rewrite quoted material.** Error strings, log lines, command output, provider
  responses, and anything inside a code fence keep their exact wording, because a
  paraphrase destroys their evidential value. Vale skips fences and masks inline `code`
  with asterisks, so it cannot tell a quote from prose, and the worker must. Code
  identifiers keep their real names.
- **Measure the fragment through a temp file.** Line numbers drift as soon as anyone edits
  above the node, so a re-run keyed to the brief's range measures the wrong prose: in one
  simulation it hid 2 of 4 new rules, in another a real regression read as flat. Copy the
  node's before text and its after text into two `.md` files, run vale on each, and report
  the rule-name diff. A zero is a control failure until you prove vale linted the file.
- **Report the alerts you acted on and the alerts you refused.** A lower count is not the
  goal, and a run that removes every alert is a failure signal.

A deslop verdict cites the vale rule, the phrase the worker changed, and the literal
`vale N->M` pair from its fragment measurement. The `script:line` citation renders only
when the doc has a tool, and it has nothing to point at for a wording change.

A first run also hits these:

- Write `slop_refused` with an underscore. The verdict parser stops at a hyphen, so
  `slop-refused` lands in the commit as verdict `slop`, reason `refused — ...`.
- A chunked section's heading and everything before the first heading belong to no node.
  The split prints how many alerts fall in those spans. Fix them by hand. Frontmatter is a
  node, bounded by its closing fence.
- One phrase repeated across nodes raises one alert per node, and each worker invents its
  own replacement. Run `dedup-pass.py` afterwards when consistency matters more than
  parallelism.
- Pass `--no-tool` to `apply-node.py` too, not only to `split-skill-dag.py`. A `--no-tool`
  split records an empty tool in the DB meta, so apply re-runs tool auto-detection.
- No vale binary means no measurement, and no clean doc either. The split says so, and each
  brief's "Vale alerts in your line range" section then carries a control-failure note
  where its alert list would have been.

## Applying proposals (the tool-change phase)

Once the doc is drained, the `proposals/` directory is a backlog. Driving it is
where most forks stall, so this phase has its own method, built on two hard-won rules:

- **Each proposal gets its own subagent and its own commit, all on ONE branch.** Do not give a worker
  several proposals and let it commit once. You lose the per-proposal review trail. Give
  it several proposals but make it commit after *each*.
- **Batch the trivial, delegate the hard.** A proposal that only appends a help line, a
  note body, or a status echo is a 3-minute worker job. Batch ~5 of these into one worker
  run over related files. A proposal that adds logic (a new note topic + its trigger, a
  parser change, a merge-tree comparison) is a single-worker or direct job. The first time
  you let a cheap worker chew on a hard proposal, it loops and times out. Pull it back and
  do it yourself.

**Parallel lanes, not one worktree.** These edits touch the same script. Run workers in
separate git worktrees (`git worktree add -b lane-N <base>`), each on a disjoint set of
proposals, then merge the lanes back with `git merge` and resolve the few conflicts by hand
(they overlap rarely, since help blocks are far apart). This is what lets you do many proposals
at once without clobbering.

**Lean gates are the difference between finishing and timing out.** Running the tool's full
test suite (or `selfcheck`, if it embeds it) after every proposal is ~2min × N and blows the
worker's budget. Instead: `bash -n` the script after each commit (instant syntax check),
and run the ONE full gate at the very end of the batch. A worker told "selfcheck after
EVERY proposal" will time out at 30min on a 5-proposal batch; told "bash -n per commit, one
selfcheck at the end", it finishes.

**Triage workers that stall.** A verification worker that loops re-reading the same file is
not "deep in thought". It is stuck. After ~15 turns with no commit, stop it, check the
worktree is clean, and take that proposal back yourself. This happened repeatedly on the
hard proposals. The fix is not a bigger hammer. The orchestrator takes the hard
one back and does it by hand.

**The commit must carry the proof.** The commit body should have the verdict lines (with
`script:line`s) that motivated the change, so a future reader can audit why a line was
added without opening the brief. `apply-node.py` now does this for the doc side; do the
same when you author tool commits by hand.

## Deliverables

When the DAG is fully drained you get:

- A **much smaller doc** (worked example cut 38% of words) whose remaining paragraphs were
  each verified accurate and non-redundant.
- A **sqlite tracking DB** linking every node's task to the commit that resolved it.
- A **`proposals/` directory** of `progressive-disclosure` rules that belong in the tool.
- When the deslop module ran, a **reworded doc**: the facts intact, a fragment-measured
  rule-name diff per node, and a `slop_refused` verdict naming every rule the doc repo's
  own writing standard overrode.
- **Auditable commits**: every trim commit includes its verdict lines (with `script:line`
  citations) in the body, so the DAG's reasoning is recoverable from `git log` alone.
- When you run the tool-change phase, a **tool PR that discloses the rules** and a
  **re-trimmed doc**. That closes the loop (see "The full loop").
- The decision-guidance core ("what you need before any tool runs") kept intact.

See [the worked example](references/WORKED-EXAMPLE.md) for the orchestrate-pi-agents run
documented end-to-end (its numbers are historical; the tool-change-phase refinements in
"The full loop" / "Applying proposals" landed after that run).

## Files

| Path | Role |
|------|------|
| `SKILL.md` | This overview. |
| `scripts/` | The pipeline runs as split, worktrees, drive, apply, and status, plus `common.py` + `prompt_modules.py` + `dedup-pass.py` (cross-node dedup) (composable trim / progressive-disclosure / deslop prompt modules). |
| `references/REFERENCE.md` | Full method: DAG, task catalog, subagent definition, wave model, apply flow, DB schema. |
| `references/WORKED-EXAMPLE.md` | The orchestrate-pi-agents run with real numbers. |

## Cross-node dedup pass

The per-node DAG checks duplication **inside** a node (does this paragraph
restate the script?). It cannot see that the same rule is stated in **two
different sections** (a golden rule repeated in the detailed step, or a
constraint stated in both the intro and a later section). `dedup-pass.py` runs a
**whole-doc** pass that spots rules stated in more than one node. It keeps the
canonical statement in one section and trims the duplicates to one-line
pointers.

```bash
# After a per-node pass has been applied, run a cross-node dedup pass:
python3 scripts/dedup-pass.py --skill PATH/TO/SKILL.md
# It prints a spawn task; the subagent reads the whole doc, finds restatements,
# and produces diffs/dedup.diff + reports/dedup.md. Apply + commit:
git -C <worktree> apply --3way -p0 --whitespace=nowarn <cache>/diffs/dedup.diff
```
