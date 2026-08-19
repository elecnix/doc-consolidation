#!/usr/bin/env python3
"""
make-worktrees.py — create one git worktree per node, branched from the current
HEAD of the doc's repo, so 5-in-parallel subagents each edit their own checkout.

Usage:
  make-worktrees.py --nodes 5        # create worktrees for the next 5 schedulable nodes
  make-worktrees.py --node 25        # create one worktree
  make-worktrees.py --cleanup        # remove all split-node worktrees + leftover branches
  make-worktrees.py --list           # list existing split-node worktrees

Each worktree: <wt-root>/<skill-name>/N%04d on branch split-node-<id>, created from
the current HEAD of the doc's repo (--skill's nearest git root). The subagent edits
ONLY its own worktree's copy of the doc; the diff it produces is against the current
HEAD, so parallel nodes never conflict.

All paths derive from --skill (see common.py). --nodes / --node / --cleanup operate
on the DB and worktrees derived from it.
"""

import argparse
import os
import sqlite3
import subprocess
import sys

from common import Ctx, git


def schedulable(conn, limit):
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
    Ctx.add_args(ap)
    ap.add_argument("--nodes", type=int, help="create worktrees for the next N schedulable nodes")
    ap.add_argument("--node", type=int, help="create one worktree")
    ap.add_argument("--cleanup", action="store_true", help="remove all split-node worktrees")
    ap.add_argument("--list", action="store_true", help="list existing split-node worktrees")
    args = ap.parse_args()
    ctx = Ctx.from_args(args)

    if args.cleanup:
        if not ctx.repo_root:
            sys.exit("no git repo found for --skill: cannot manage worktrees")
        # 1) remove every worktree under wt_root whose branch matches the prefix
        #    (scoped to wt_root, not wt_dir, so leftovers from earlier slug layouts
        #    are also caught)
        porcelain = git(ctx.repo_root, "worktree", "list", "--porcelain").stdout
        lines = porcelain.split("\n")
        removed = 0
        cur = None
        cur_branch = None
        for ln in lines:
            if ln.startswith("worktree "):
                cur = ln.split(" ", 1)[1]
                cur_branch = None
            elif ln.startswith("branch refs/heads/"):
                cur_branch = ln.split("refs/heads/", 1)[1]
            elif cur and cur_branch and ln == "":
                under = os.path.realpath(cur).startswith(os.path.realpath(ctx.wt_root))
                if under and cur_branch.startswith(ctx.branch_prefix):
                    rr = git(ctx.repo_root, "worktree", "remove", cur)
                    print("removed %s %s" % (cur, "" if rr.returncode == 0 else ("(failed: %s)" % rr.stderr.strip()[:120])))
                    removed += 1
                cur = None
                cur_branch = None
        # 2) drop split-node branches that have no live worktree registration
        #    (worktree remove de-registers; prune cleans dead registrations)
        git(ctx.repo_root, "worktree", "prune")
        live_branches = {
            ln.split("refs/heads/", 1)[1]
            for ln in git(ctx.repo_root, "worktree", "list", "--porcelain").stdout.split("\n")
            if ln.startswith("branch refs/heads/")
        }
        dropped = 0
        branches = [b.strip() for b in git(ctx.repo_root, "branch", "--list", ctx.branch_prefix + "*").stdout.split("\n") if b.strip()]
        for b in branches:
            if b not in live_branches:
                rr = git(ctx.repo_root, "branch", "-D", b)
                if rr.returncode == 0:
                    print("removed branch %s" % b)
                    dropped += 1
        print("cleanup done (%d worktrees, %d branches)" % (removed, dropped))
        return

    if args.list:
        if not ctx.repo_root:
            print("no repo found for --skill: %s" % ctx.skill_path)
            return
        r = git(ctx.repo_root, "worktree", "list", "--porcelain")
        for line in r.stdout.split("\n"):
            if line.startswith("worktree "):
                wt = line.split(" ", 1)[1]
                if os.path.realpath(wt).startswith(os.path.realpath(ctx.wt_dir)):
                    print(wt)
        return

    if not ctx.repo_root:
        sys.exit("--skill must live in a git repo to create worktrees: %s" % ctx.skill_path)

    conn = sqlite3.connect(ctx.db)
    if args.node:
        ids = [args.node]
    elif args.nodes:
        ids = [r[0] for r in schedulable(conn, args.nodes)]
    else:
        ap.error("--node or --nodes required")
    conn.close()

    os.makedirs(ctx.wt_dir, exist_ok=True)
    live_branches = {
        ln.split("refs/heads/", 1)[1]
        for ln in git(ctx.repo_root, "worktree", "list", "--porcelain").stdout.split("\n")
        if ln.startswith("branch refs/heads/")
    }
    for nid in ids:
        wt = ctx.worktree_path(nid)
        branch = ctx.worktree_branch(nid)
        if os.path.exists(wt):
            print("exists: %s" % wt)
            continue
        if branch not in live_branches:
            # leftover branch from a previous run (worktree removed, branch not)
            rr = git(ctx.repo_root, "branch", "-D", branch)
            if rr.returncode == 0:
                print("dropped leftover branch %s" % branch)
        r = git(ctx.repo_root, "worktree", "add", "-b", branch, wt)
        if r.returncode:
            print("FAIL %d: %s" % (nid, r.stderr.strip()))
        else:
            print("created %s (branch %s)" % (wt, branch))


if __name__ == "__main__":
    main()
