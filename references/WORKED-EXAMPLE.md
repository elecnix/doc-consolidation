# Worked example: orchestrate-pi-agents

> This is a historical record of the first real run. "fleet" was that skill's tool.
> Substitute your own doc's tool everywhere you see it. The method is tool-agnostic.

The first real use of doc-consolidation was shrinking the
`orchestrate-pi-agents` skill (`~/.agents/skills/orchestrate-pi-agents/SKILL.md`),
whose fleet tool (`scripts/fleet`, 920KB) carries the reasoning for every recipe in its
`doc_` strings and `fleet note` bodies.

## Before

- **370,945 bytes / 5,587 lines / 61,625 words**
- 185 headings, 1132 blank-line-separated blocks, 109 code blocks
- The fleet script's `fleet help <verb>` output and 26 `fleet note` topics already taught
  most of the mechanics the prose restated.

## After

- **231,445 bytes / 3,415 lines / 38,482 words** (38% bytes, 39% lines, 38% words cut)
- The trimmed file passes the real `fleet selfcheck` (80 verbs, 26 notes, fences balanced,
  all `fleet <verb>` invocations resolve, all `fleet note <topic>` topics exist).

## The run

- **216 nodes** (144 sections + 70 chunks + 1 frontmatter + 1 H1), 224 edges (166 hierarchy,
  48 chunk, 10 cross-reference), 748 tasks.
- **37 waves of 5** parallel subagents (`context: "fresh"`), ~1h40m wall time.
- **150 commits** on a `skill-split-dag` branch (one per node that changed the file), merged
  to `main` and pushed.
- **149 nodes trimmed**, 67 kept as-is.
- Task verdicts: 270 trim-to-pointer and 463 keep, plus 10 delete and 3 move-to-fleet.
- **3 move-to-fleet proposals** accumulated for a separate fleet-tool subagent:
  stale-green detection in `fleet prs`, a stale `doc_board` help string, and a
  worktree-before-spawn recipe.

## Accuracy drifts the subagents caught

10+ real accuracy drifts, exactly what `verify-accuracy` was for:

- A quota sample output block hardcoded "Session (resets ~5h) / Weekly (resets 7d)" but the
  tool now derives reset cadence from observed drops.
- A paragraph claimed `fleet status` has "six buckets" but the tool's list has 8.
- A `fleet cmux spawn` paragraph claimed a `--session` flag that does not exist.
- A stale cross-reference to a section ("The gap that remains") that no longer exists.
- An order-file example used `then` where the script's parser expects `after`.
- A routing-order paragraph said "~100k tokens" but the tool says "~72k tokens measured".
- A heartbeat code-block comment listed "staging, budget, agents, queue" but the tool's
  actual routine block differs.

## What stayed (the decision-guidance core)

The 463 `keep` verdicts are the paragraphs the tool does not cover: how to decide and how
to report, together with the delegation calls and the failure modes of monitoring itself,
vocabulary rules (the word
is "spawn"), operator rulings, and incident illustrations (BROKEN/FIXED pairs, measured
numbers). These are what an orchestrator needs **before any tool runs**. They cannot wait
for a verb to fire.
