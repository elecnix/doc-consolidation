#!/usr/bin/env bash
# dedup-pass.py — a whole-doc pass that spots duplication BETWEEN nodes.
#
# The per-node DAG checks duplication INSIDE a node (does this paragraph restate
# the script?). It cannot see that the same rule is stated in two different
# sections — e.g. a golden rule repeated in the detailed step, or a constraint
# stated in both the intro and a later section. This pass reads the WHOLE doc
# and finds rules/concepts stated in more than one node, keeping the canonical
# statement and trimming the duplicates to one-line pointers.
#
# It writes a dedup brief (the whole doc + the task) and prints the spawn task
# for the orchestrator, like drive-nodes.py. The subagent runs fresh-context in
# the split worktree, produces diffs/dedup.diff + reports/dedup.md, and reports.

import argparse
import os
import sys

from common import Ctx

TASK = """You are running a CROSS-NODE DEDUP pass on the whole doc below.

GOAL: find every rule, principle, or concept that is stated in MORE THAN ONE
section, and consolidate — keep the most detailed/contextual statement in ONE
section (the canonical home), and trim every other occurrence to a one-line
pointer that names the canonical section. The doc should say each thing once.

How to do it:
1. Read the whole doc. Build a mental index: for each distinct rule/principle,
   which sections state it.
2. For each rule stated in 2+ sections, pick the canonical home — usually the
   most detailed or most natural section (a golden rule stays a golden rule; a
   detailed step keeps the full reasoning). Trim the OTHER occurrences to a
   one-line pointer: "See <canonical section heading> for <what>."
3. Do NOT merge sections, do NOT delete a rule, do NOT change behavior — only
   collapse restatements to pointers. Keep the canonical statement verbatim.
4. Beware false positives: a rule that is stated in two sections for a REASON
   (e.g. a golden-rule one-liner that is the quick reference, and the full
   reasoning in the detailed step) is still duplication — the golden rule can
   point at the detailed step, OR the detailed step can point at the golden
   rule. Pick the home that a reader scanning the doc would hit first; usually
   that is the detailed step (the golden rule is the summary, so it points).
   But if the golden rule is the load-bearing one-liner and the detailed step
   only elaborates, keep the golden rule and trim the elaboration to a pointer
   — judge per case, and say which you picked and why in the report.

OUTPUT:
- Produce the diff: `git -C {wt} diff --no-prefix -- {skill_rel} > {diff_out}`
- Write a report to {report_out}: one block per duplicated rule, naming
  (a) the rule, (b) the sections it appeared in (with line numbers), (c) which
  section is the canonical home and why, (d) which occurrences were trimmed to
  pointers.
- Report back: how many rules you deduped, and the diff + report paths.

Rules:
- Edit ONLY {wt}/{skill_rel}. Never commit, never push.
- Do not invent new rules — only collapse existing restatements.
- If two sections state a rule with materially different wording that adds
  distinct value, keep both and note "kept both: distinct value" in the report
  rather than forcing a pointer.
- Aim to finish in under 5 minutes.
"""


def main():
    ap = argparse.ArgumentParser(description="Cross-node dedup pass: spot duplication between nodes.")
    Ctx.add_args(ap)
    ap.add_argument("--model", default="deepseek-v4-flash:0731",
                    help="subagent model (default: deepseek-v4-flash:0731)")
    args = ap.parse_args()
    ctx = Ctx.from_args(args)

    doc = open(ctx.skill_path, encoding="utf-8").read()
    ctx.ensure_dirs()
    # The subagent edits the split worktree's copy of the doc (where a per-node
    # pass has already been applied). wt is the worktree root; skill_rel is the
    # doc path relative to it, so wt/skill_rel is the actual file to edit.
    wt = ctx.repo_root or os.path.dirname(ctx.skill_path)
    diff_out = os.path.join(ctx.diffs_dir, "dedup.diff")
    report_out = os.path.join(ctx.base, "reports", "dedup.md")
    os.makedirs(os.path.dirname(report_out), exist_ok=True)

    brief_path = os.path.join(ctx.briefs_dir, "dedup.md")
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write("# Cross-node dedup pass\n\n")
        f.write(TASK.format(wt=wt, skill_rel=ctx.skill_rel, diff_out=diff_out, report_out=report_out))
        f.write("\n## The whole doc\n\n```\n" + doc + "\n```\n")

    print("=" * 70)
    print("DEDUP PASS — %s" % brief_path)
    print("=" * 70)
    print("Spawn ONE fresh-context subagent (context: fresh, model: %s) with this task:" % args.model)
    print()
    print("Read the brief at %s and execute it exactly. It contains the whole" % brief_path)
    print("doc + the cross-node dedup task. Edit ONLY %s/%s. Produce the diff" % (wt, ctx.skill_rel))
    print("at %s and the report at %s. Report back the dedup count + paths." % (diff_out, report_out))
    print()
    print("When the subagent reports, apply the diff:")
    print("  git -C %s apply --3way -p0 --whitespace=nowarn %s" % (wt, diff_out))
    print("then commit %s in the split worktree." % ctx.skill_rel)


if __name__ == "__main__":
    main()