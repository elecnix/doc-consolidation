#!/usr/bin/env python3
"""
drive-nodes.py — list schedulable nodes; print the subagent spawn task per node.

Each node's brief (in <cache>/briefs/N%04d-*.md) is handed to one fresh-context
subagent. The subagent investigates the paragraph against the tool, writes its diff +
verdicts, and reports back. This driver prints the ready-to-run spawn task; the caller
(the orchestrator session) runs it via the subagent tool with context: "fresh".

Workers MUST use fresh context, never fork. A forked worker inherits the orchestrator's
session — including these pipeline scripts and the sqlite DB — and on a real run one ran
`apply-node.py` itself, committing to the shared split branch past the orchestrator's
verification gate. Fresh context isolates the worker to its brief and worktree.

All paths derive from --skill (see common.py).

Usage:
  drive-nodes.py --nodes 3            # print spawn tasks for the next 3 schedulable nodes
  drive-nodes.py --node 5             # print one specific node's spawn task
  drive-nodes.py --status             # DB progress
  drive-nodes.py --next 10            # list the next 10 schedulable nodes

--model sets the subagent model in the printed spawn task (default:
deepseek-v4-flash — the cheap worker; pass --model <anything> to override. The
default appends `:low` to set low thinking per child (the only per-child thinking
mechanism that works in a workflowScript — a bare `thinking:` field on the item is
silently ignored). Drop the `:low` suffix if verdict quality drops on a harder doc.
"""

import argparse
import sqlite3
import sys

from common import Ctx


def db_conn(ctx):
    conn = sqlite3.connect(ctx.db)
    conn.row_factory = sqlite3.Row
    return conn


def schedulable(conn, limit):
    cur = conn.cursor()
    return cur.execute("""
        SELECT n.id, n.heading, n.kind, n.word_count
        FROM nodes n
        WHERE n.status = 'pending'
          AND NOT EXISTS (
            SELECT 1 FROM edges e
            JOIN nodes d ON d.id = e.depends_on
            WHERE e.node_id = n.id AND d.status != 'done'
          )
        ORDER BY n.id LIMIT ?
    """, (limit,)).fetchall()


def main():
    ap = argparse.ArgumentParser()
    Ctx.add_args(ap)
    ap.add_argument("--nodes", type=int, help="print spawn tasks for the next N schedulable nodes")
    ap.add_argument("--node", type=int, help="print one specific node's spawn task")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--next", type=int, help="list the next N schedulable nodes")
    ap.add_argument("--model", default="deepseek-v4-flash:low",
                    help="subagent model for the spawn task (default: deepseek-v4-flash:low — the :low suffix sets low thinking per child via resolveEffectiveThinking(); drop the suffix to restore the agent default if verdict quality drops)")
    ap.add_argument("--move-to-tool", action="store_true",
                    help="spawn task biases toward move-to-tool proposals: for each decision rule / failure-mode / never-always rule about a script's behavior, propose embedding it in the script's header/--help/code-comment if the script does not already state it. Use as a second pass after a strict-trim run, to surface rules that can leave the doc once the tool self-describes them.")
    args = ap.parse_args()
    ctx = Ctx.from_args(args)

    conn = db_conn(ctx)

    if args.status:
        cur = conn.cursor()
        print("nodes:", dict(cur.execute("SELECT status, COUNT(*) FROM nodes GROUP BY status").fetchall()))
        print("tasks:", dict(cur.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()))
        return

    if args.next:
        for r in schedulable(conn, args.next):
            print("node %d %s (%s, %dw)" % (r["id"], r["heading"], r["kind"], r["word_count"]))
        return

    if args.node:
        ids = [args.node]
    elif args.nodes:
        ids = [r["id"] for r in schedulable(conn, args.nodes)]
    else:
        ap.error("--node or --nodes required")

    for nid in ids:
        brief = ctx.brief_path(nid)
        if not brief:
            print("no brief for node %d (run split-skill-dag.py first?)" % nid)
            continue
        print("=" * 70)
        print("NODE %d — %s" % (nid, brief))
        print("=" * 70)
        print("Spawn ONE fresh-context subagent (context: fresh, model: %s) with this task:" % args.model)
        print()
        if args.move_to_tool:
            print("Read the brief at %s for the paragraph text and section context." % brief)
            print("GOAL: find rules that belong EMBEDDED in a tool script, not in the doc —")
            print("move-to-tool proposals. For each decision rule, failure-mode warning,")
            print("never/always rule, or behavioral constraint in the paragraph:")
            print("  1. Identify which tool script the rule is ABOUT (the script whose behavior")
            print("     the rule governs).")
            print("  2. Run `<worktree>/tools/ci/<script> --help` (or the script's header) and")
            print("     grep the script for the concept; read the code comments near the code.")
            print("  3. If the script ALREADY states the rule -> trim the doc to a pointer.")
            print("     If it does NOT, but the rule is ABOUT that script's behavior/invocation")
            print("     -> MOVE-TO-TOOL: write a proposal naming (a) the rule verbatim, (b) the")
            print("     target script + exact location (header/--help/code-comment at line N),")
            print("     (c) the proposed wording to embed, (d) why the tool is the right home.")
            print("     Keep a rule in the doc ONLY if it is genuine cross-script orchestration")
            print("     no single script can self-describe.")
            print("BIAS TOWARD PROPOSING move-to-tool — when in doubt, propose; the orchestrator")
            print("decides whether to implement. Then trim each moved rule to a one-line")
            print("pointer, produce the diff, and write verdicts citing script:line. IMPORTANT:")
            print("do NOT edit any tool script — only write the proposal. Finish in under 4 min.")
        else:
            print("Read the brief at %s and execute it exactly." % brief)
            print("Apply the brief's strict trimming policy: only trim text VERBATIM restated in")
            print("the tool's header / a code comment / --help / a note; keep every decision rule,")
            print("failure-mode illustration, and never/always rule even if partially discoverable.")
            print("When done, report: your verdicts, one-line reasons, and the diff path.")
            print("Every verdict line MUST cite the specific script:line verified (e.g. open-pr.sh:7-10)")
            print("-- the orchestrator writes those citations into the commit body.")
        print()


if __name__ == "__main__":
    main()
