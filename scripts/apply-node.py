#!/usr/bin/env python3
"""
apply-node.py — apply one node's diff to the worktree SKILL.md, commit, record.

Each node's subagent produces a diff (against the pristine original snapshot) plus a
verdicts file. This script:
  1. reads the node's diff and verdicts
  2. applies the diff to the worktree SKILL.md (atomic write, never in place)
  3. runs the cheap doc/script agreement checks (fences balanced, every `fleet <verb>`
     in a code block resolves, every `fleet note <topic>` exists)
  4. commits SKILL.md with an explicit path (never `git add -A`), one commit per node
  5. records the commit id on every task of the node that is marked done

Usage:
  apply-node.py NODE_ID [--db PATH] [--skill PATH] [--fleet PATH] [--dry-run]
  apply-node.py --status [--db PATH]
  apply-node.py --next [N] [--db PATH]     # schedulable nodes (deps done)
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time

BASE = os.path.expanduser("~/.cache/fleet-skill-split")
DB = os.path.join(BASE, "skill-dag.db")
SKILL = os.path.expanduser("~/Source/orchestrate-pi-agents-split/SKILL.md")
FLEET = os.path.expanduser("~/.agents/skills/orchestrate-pi-agents/scripts/fleet")


def db_conn(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def atomic_write(path, content):
    """Same-dir temp + mv — a concurrent reader sees whole old or whole new, never partial."""
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def check_doc_agreement(skill_path, fleet_path):
    """Cheap half of fleet selfcheck: fences balanced, fleet verbs resolve, note topics exist."""
    problems = []
    with open(skill_path, encoding="utf-8") as f:
        doc = f.read()
    fences = doc.count("```")
    if fences % 2:
        problems.append("unbalanced fences (%d)" % fences)

    # verbs dispatched in the script
    verbs = set()
    try:
        src = open(fleet_path, encoding="utf-8").read()
        mm = re.search(r"^main\(\) \{(.*?)^\}", src, re.S | re.M)
        if mm:
            verbs = set()
            for g in re.findall(r"^    ([a-z|-]+)\)", mm.group(1), re.M):
                verbs.update(v for v in g.split("|") if v not in ("-h", "--help", "--version"))
    except OSError:
        problems.append("cannot read fleet script")

    # every fleet <verb> inside a fenced bash block must resolve
    inb = False
    for line in doc.split("\n"):
        if line.startswith("```bash"):
            inb = True
            continue
        if line.startswith("```"):
            inb = False
            continue
        if inb:
            for v in re.findall(r"\bfleet ([a-z][a-z-]*)", line):
                if v not in verbs:
                    problems.append("doc invokes 'fleet %s' — no such verb" % v)

    # note topics
    topics = set()
    try:
        src = open(fleet_path, encoding="utf-8").read()
        m = re.search(r"^note_topics\(\) \{(.*?)^\}", src, re.S | re.M)
        if m:
            topics = set(re.findall(r"\b([a-z][a-z-]+)\b", m.group(1)))
    except OSError:
        pass
    for t in set(re.findall(r"fleet note ([a-z-]+)", doc)):
        if t not in topics:
            problems.append("doc names note '%s' — no such topic" % t)
    return problems


def apply_diff(skill_path, diff_path):
    """Apply a unified diff to the worktree file. Returns (ok, detail)."""
    if not os.path.exists(diff_path) or os.path.getsize(diff_path) == 0:
        return True, "no diff (keep)"
    with open(diff_path, encoding="utf-8") as f:
        diff = f.read()
    # The subagent diffed its own worktree (branched from the current split-branch HEAD),
    # so the diff applies to the shared worktree as-is. git apply --3way merges against
    # the index, so a hunk whose context drifted after a sibling node's trim still lands.
    repo = os.path.dirname(skill_path)
    r = subprocess.run(
        ["git", "-C", repo, "apply", "--3way", "-p0", "--whitespace=nowarn", "-"],
        input=diff, capture_output=True, text=True)
    if r.returncode == 0:
        return True, "applied (git apply --3way)"
    return False, "git apply failed: %s" % r.stderr.strip()[:200]


def commit_node(skill_path, node_id, heading, task_ids, dry_run):
    repo = os.path.dirname(skill_path)
    r = git(repo, "status", "--short", "--", "SKILL.md")
    if r.stdout.strip():
        # stage only SKILL.md — never git add -A (shared repo rule)
        r = git(repo, "add", "--", "SKILL.md")
        if r.returncode:
            return None, "git add failed: %s" % r.stderr
        msg = "skill-split: node %d %s (%s)\n\nCo-authored-by: noble-tiger-50 <noble-tiger-50@pi-agent.local>" % (node_id, heading[:60], ",".join(task_ids))
        if dry_run:
            return "DRY-RUN", "would commit: %s" % msg
        r = git(repo, "-c", "core.editor=false", "commit", "-m", msg, "--", "SKILL.md")
        if r.returncode:
            return None, "commit failed: %s" % r.stderr
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        return sha, msg
    return None, "no change to SKILL.md (keep verdict)"


def mark_done(conn, node_id, commit_id, verdicts_path):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    cur.execute("UPDATE nodes SET status='done', notes=? WHERE id=?", (commit_id or "keep", node_id))
    cur.execute("UPDATE tasks SET status='done', commit_id=?, done_at=? WHERE node_id=?", (commit_id, now, node_id))
    conn.commit()


def read_verdicts(verdicts_path):
    """Return {task_id: (kind, verdict, reason)} from the subagent's verdicts file."""
    out = {}
    if not os.path.exists(verdicts_path):
        return out
    for line in open(verdicts_path, encoding="utf-8"):
        line = line.strip()
        m = re.match(r"^(T\d{4})\s+([a-z-]+):\s*(\w+)\s*[—-]?\s*(.*)$", line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3), m.group(4))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("node_id", nargs="?", type=int, default=None)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--skill", default=SKILL)
    ap.add_argument("--fleet", default=FLEET)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--next", nargs="?", const=10, type=int)
    args = ap.parse_args()

    conn = db_conn(args.db)

    if args.status:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT status, COUNT(*) FROM nodes GROUP BY status").fetchall()
        print("nodes:", dict(rows))
        rows = cur.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
        print("tasks:", dict(rows))
        return

    if args.next:
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT n.id, n.heading, n.kind, n.word_count
            FROM nodes n
            WHERE n.status = 'pending'
              AND NOT EXISTS (
                SELECT 1 FROM edges e
                JOIN nodes d ON d.id = e.depends_on
                WHERE e.node_id = n.id AND d.status != 'done'
              )
            ORDER BY n.id LIMIT ?
        """, (args.next,)).fetchall()
        for r in rows:
            print("node %d %s (%s, %dw)" % (r[0], r[1], r[2], r[3]))
        return

    if args.node_id is None:
        ap.error("node_id required (or --status / --next)")

    cur = conn.cursor()
    node = cur.execute("SELECT * FROM nodes WHERE id=?", (args.node_id,)).fetchone()
    if not node:
        sys.exit("no such node: %d" % args.node_id)
    if node["status"] == "done":
        sys.exit("node %d already done" % args.node_id)

    tasks = cur.execute("SELECT * FROM tasks WHERE node_id=? ORDER BY id", (args.node_id,)).fetchall()
    task_ids = [t["id"] for t in tasks]

    diff_path = os.path.join(BASE, "diffs", "N%04d.diff" % args.node_id)
    verdicts_path = os.path.join(BASE, "verdicts", "N%04d.md" % args.node_id)

    ok, detail = apply_diff(args.skill, diff_path)
    if not ok:
        print("CONFLICT node %d: %s" % (args.node_id, detail))
        cur.execute("UPDATE nodes SET status='conflict' WHERE id=?", (args.node_id,))
        conn.commit()
        sys.exit(1)

    problems = check_doc_agreement(args.skill, args.fleet)
    if problems:
        print("AGREEMENT FAIL node %d:" % args.node_id)
        for p in problems:
            print("  - %s" % p)
        cur.execute("UPDATE nodes SET status='conflict' WHERE id=?", (args.node_id,))
        conn.commit()
        sys.exit(1)

    sha, msg = commit_node(args.skill, args.node_id, node["heading"], task_ids, args.dry_run)
    if sha is None and msg.startswith("commit failed"):
        print("COMMIT FAIL node %d: %s" % (args.node_id, msg))
        sys.exit(1)

    if args.dry_run:
        print("DRY-RUN node %d: %s (%s)" % (args.node_id, node["heading"][:50], sha or "keep"))
        return

    verdicts = read_verdicts(verdicts_path)
    mark_done(conn, args.node_id, sha, verdicts_path)
    print("node %d done: %s (%s)" % (args.node_id, node["heading"][:50], sha or "keep"))
    if verdicts:
        for tid, (kind, verdict, reason) in sorted(verdicts.items()):
            print("  %s %s: %s — %s" % (tid, kind, verdict, reason[:80]))


if __name__ == "__main__":
    main()
