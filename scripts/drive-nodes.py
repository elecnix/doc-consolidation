#!/usr/bin/env python3
"""
drive-nodes.py — list schedulable nodes; print the subagent spawn task per node.

Each node's brief (in <cache>/briefs/N%04d-*.md) is handed to one forked subagent.
The subagent investigates the paragraph against the tool, writes its diff + verdicts,
and reports back. This driver prints the ready-to-run spawn task; the caller (the
orchestrator session) runs it via the subagent tool with context: "fork".

All paths derive from --skill (see common.py).

Usage:
  drive-nodes.py --nodes 3            # print spawn tasks for the next 3 schedulable nodes
  drive-nodes.py --node 5             # print one specific node's spawn task
  drive-nodes.py --status             # DB progress
  drive-nodes.py --next 10            # list the next 10 schedulable nodes

--model sets the subagent model in the printed spawn task (default:
deepseek-v4-flash — the cheap worker; pass --model <anything> to override).
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
    ap.add_argument("--model", default="deepseek-v4-flash",
                    help="subagent model for the spawn task (default: deepseek-v4-flash)")
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
        print("Spawn ONE forked subagent (context: fork, model: %s) with this task:" % args.model)
        print()
        print("Read the brief at %s and execute it exactly." % brief)
        print("The brief tells you what to investigate, what verdicts to produce,")
        print("and where to write the diff and verdicts files.")
        print("When done, report: your verdicts, one-line reasons, and the diff path.")
        print()


if __name__ == "__main__":
    main()
