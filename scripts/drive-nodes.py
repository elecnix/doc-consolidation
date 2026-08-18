#!/usr/bin/env python3
"""
drive-nodes.py — spawn one forked subagent per schedulable node, apply, commit, record.

Each node's brief (in ~/.cache/fleet-skill-split/briefs/N%04d-*.md) is handed to one
forked subagent. The subagent investigates the paragraph against the fleet tool, writes
its diff + verdicts, and reports back. This driver then applies the diff to the worktree
SKILL.md, runs the doc/script agreement checks, commits (one commit per node), and
records the commit id on the node's tasks.

Usage:
  drive-nodes.py --nodes 3            # spawn subagents for the next 3 schedulable nodes
  drive-nodes.py --node 5             # spawn one specific node
  drive-nodes.py --status             # DB progress
  drive-nodes.py --next 10            # list the next 10 schedulable nodes

The subagent invocation is printed as a ready-to-run command; the caller (the
orchestrator session) runs it via the subagent tool with context: "fork".
"""

import argparse
import glob
import os
import sqlite3
import sys

BASE = os.path.expanduser("~/.cache/fleet-skill-split")
DB = os.path.join(BASE, "skill-dag.db")
BRIEFS = os.path.join(BASE, "briefs")


def db_conn():
    conn = sqlite3.connect(DB)
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


def brief_for(node_id):
    matches = glob.glob(os.path.join(BRIEFS, "N%04d-*.md" % node_id))
    return matches[0] if matches else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, help="spawn subagents for the next N schedulable nodes")
    ap.add_argument("--node", type=int, help="spawn one specific node")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--next", type=int, help="list the next N schedulable nodes")
    args = ap.parse_args()

    conn = db_conn()

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
        brief = brief_for(nid)
        if not brief:
            print("no brief for node %d" % nid)
            continue
        print("=" * 70)
        print("NODE %d — %s" % (nid, brief))
        print("=" * 70)
        print("Spawn ONE forked subagent (context: fork) with this task:")
        print()
        print("Read the brief at %s and execute it exactly." % brief)
        print("The brief tells you what to investigate, what verdicts to produce,")
        print("and where to write the diff and verdicts files.")
        print("When done, report: your verdicts, one-line reasons, and the diff path.")
        print()


if __name__ == "__main__":
    main()
