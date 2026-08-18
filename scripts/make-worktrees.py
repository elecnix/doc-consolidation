#!/usr/bin/env python3
"""
make-worktrees.py — create one git worktree per node, branched from the current
split-branch HEAD, so 5-in-parallel subagents each edit their own checkout.

Usage:
  make-worktrees.py --nodes 5        # create worktrees for the next 5 schedulable nodes
  make-worktrees.py --node 25        # create one worktree
  make-worktrees.py --cleanup        # remove all split-node worktrees (after merge)

Each worktree: ~/Source/skill-split-wt/N%04d on branch split-node-<id>, created from
the current HEAD of the shared split worktree (~/Source/orchestrate-pi-agents-split).
The subagent edits ONLY its own worktree's SKILL.md; the diff it produces is against
the current split-branch state, so parallel nodes never conflict.
"""

import argparse
import os
import subprocess
import sys

SPLIT = os.path.expanduser("~/Source/orchestrate-pi-agents-split")
WT_ROOT = os.path.expanduser("~/Source/skill-split-wt")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def schedulable(conn, limit):
    import sqlite3
    cur = conn.cursor()
    return cur.execute("""
        SELECT n.id, n.heading FROM nodes n
        WHERE n.status = 'pending'
          AND NOT EXISTS (
            SELECT 1 FROM edges e JOIN nodes d ON d.id = e.depends_on
            WHERE e.node_id = n.id AND d.status != 'done'
          )
        ORDER BY n.id LIMIT ?
    """, (limit,)).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int)
    ap.add_argument("--node", type=int)
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    if args.cleanup:
        r = git(SPLIT, "worktree", "list", "--porcelain")
        for line in r.stdout.split("\n"):
            if line.startswith("worktree "):
                wt = line.split(" ", 1)[1]
                if wt.startswith(WT_ROOT):
                    git(SPLIT, "worktree", "remove", wt)
                    print("removed", wt)
        return

    import sqlite3
    conn = sqlite3.connect(os.path.expanduser("~/.cache/fleet-skill-split/skill-dag.db"))
    if args.node:
        ids = [args.node]
    elif args.nodes:
        ids = [r[0] for r in schedulable(conn, args.nodes)]
    else:
        ap.error("--node or --nodes required")

    os.makedirs(WT_ROOT, exist_ok=True)
    for nid in ids:
        wt = os.path.join(WT_ROOT, "N%04d" % nid)
        branch = "split-node-%d" % nid
        if os.path.exists(wt):
            print("exists: %s" % wt)
            continue
        r = git(SPLIT, "worktree", "add", "-b", branch, wt)
        if r.returncode:
            print("FAIL %d: %s" % (nid, r.stderr.strip()))
        else:
            print("created %s (branch %s)" % (wt, branch))


if __name__ == "__main__":
    main()
