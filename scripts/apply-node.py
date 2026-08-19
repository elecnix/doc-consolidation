#!/usr/bin/env python3
"""
apply-node.py — apply one node's diff to the repo SKILL.md, commit, record.

Each node's subagent produces a diff (against the current HEAD of the doc's repo)
plus a verdicts file. This script:
  1. reads the node's diff and verdicts
  2. applies the diff to the repo's copy of the doc (atomic write, never in place)
  3. runs the cheap doc/tool agreement checks (fences balanced, every `<tool> <verb>`
     in a code block resolves, every `<tool> note <topic>` exists) — skipped when
     --no-tool / no tool detected
  4. commits the doc with an explicit path (never `git add -A`), one commit per node
  5. records the commit id on every task of the node that is marked done

All paths derive from --skill (see common.py); the DB remembers the tool and
co-author so a later apply-node run needs no flags beyond the node id.

Usage:
  apply-node.py NODE_ID [--db PATH] [--skill PATH] [--tool PATH] [--no-tool]
                [--co-author NAME] [--dry-run]
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

from common import Ctx, default_db_for, git


def db_conn(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def atomic_write(path, content):
    """Same-dir temp + mv — a concurrent reader sees whole old or whole new, never partial."""
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def check_doc_agreement(ctx):
    """Cheap half of a selfcheck: fences balanced, verbs resolve, note topics exist.

    Tool-dependent checks are skipped when no tool is detected (--no-tool or no
    matching script). The doc is read from the repo's copy (ctx.skill_rel) and the
    tool script from ctx.tool.
    """
    problems = []
    doc_path = os.path.join(ctx.repo_root, ctx.skill_rel) if ctx.repo_root else ctx.skill_path
    with open(doc_path, encoding="utf-8") as f:
        doc = f.read()
    fences = doc.count("```")
    if fences % 2:
        problems.append("unbalanced fences (%d)" % fences)

    if not ctx.tool or not os.path.exists(ctx.tool):
        return problems

    verbs = set()
    try:
        src = open(ctx.tool, encoding="utf-8").read()
        mm = re.search(r"^main\(\) \{(.*?)^\}", src, re.S | re.M)
        if mm:
            verbs = set()
            for g in re.findall(r"^    ([a-z|-]+)\)", mm.group(1), re.M):
                verbs.update(v for v in g.split("|") if v not in ("-h", "--help", "--version"))
    except OSError:
        problems.append("cannot read tool script: %s" % ctx.tool)
        return problems

    # every <tool> <verb> inside a fenced bash block must resolve
    inb = False
    for line in doc.split("\n"):
        if line.startswith("```bash"):
            inb = True
            continue
        if line.startswith("```"):
            inb = False
            continue
        if inb:
            for v in re.findall(r"\b%s ([a-z][a-z-]*)" % re.escape(ctx.tool_name), line):
                if v not in verbs:
                    problems.append("doc invokes '%s %s' — no such verb" % (ctx.tool_name, v))

    # note topics
    topics = set()
    try:
        src = open(ctx.tool, encoding="utf-8").read()
        m = re.search(r"^note_topics\(\) \{(.*?)^\}", src, re.S | re.M)
        if m:
            topics = set(re.findall(r"\b([a-z][a-z-]+)\b", m.group(1)))
    except OSError:
        pass
    for t in set(re.findall(r"%s note ([a-z-]+)" % re.escape(ctx.tool_name), doc)):
        if t not in topics:
            problems.append("doc names note '%s' — no such topic" % t)
    return problems


def apply_diff(ctx, diff_path):
    """Apply a unified diff to the repo's copy of the doc. Returns (ok, detail)."""
    if not os.path.exists(diff_path) or os.path.getsize(diff_path) == 0:
        return True, "no diff (keep)"
    with open(diff_path, encoding="utf-8") as f:
        diff = f.read()
    if not ctx.repo_root:
        # no repo: apply against the file in place, context-only
        r = subprocess.run(
            ["patch", "-p0", "--forward", "--batch", ctx.skill_path],
            input=diff, capture_output=True, text=True)
        if r.returncode == 0:
            return True, "applied (patch -p0)"
        return False, "patch failed: %s" % r.stderr.strip()[:200]
    # The subagent diffed its own worktree (branched from the current repo HEAD), so
    # the diff applies to the repo copy as-is. git apply --3way merges against the
    # index, so a hunk whose context drifted after a sibling node's trim still lands.
    r = subprocess.run(
        ["git", "-C", ctx.repo_root, "apply", "--3way", "-p0", "--whitespace=nowarn", "-"],
        input=diff, capture_output=True, text=True)
    if r.returncode == 0:
        return True, "applied (git apply --3way)"
    return False, "git apply failed: %s" % r.stderr.strip()[:200]


def commit_node(ctx, node_id, heading, task_ids, dry_run):
    """Commit the doc change in the repo. Returns (sha|None, msg).

    With dry_run=True the change is left staged but never committed.
    """
    repo = ctx.repo_root
    if not repo:
        return None, "no git repo for %s — change left uncommitted" % ctx.skill_path
    r = git(repo, "status", "--short", "--", ctx.skill_rel)
    if not r.stdout.strip():
        return None, "no change to %s (keep verdict)" % ctx.skill_rel
    # stage only the doc — never git add -A (shared repo rule)
    r = git(repo, "add", "--", ctx.skill_rel)
    if r.returncode:
        return None, "git add failed: %s" % r.stderr
    msg = "skill-split: node %d %s (%s)" % (node_id, heading[:60], ",".join(task_ids))
    trailer = ctx.coauthor_trailer()
    if trailer:
        msg += "\n\n%s" % trailer
    if dry_run:
        return "DRY-RUN", "would commit: %s" % msg
    r = git(repo, "-c", "core.editor=false", "commit", "-m", msg, "--", ctx.skill_rel)
    if r.returncode:
        return None, "commit failed: %s" % r.stderr
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    return sha, msg


def mark_done(conn, node_id, commit_id):
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


def meta_row(conn, key, default=""):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def ctx_from_db_meta(conn, db, args):
    """Build a Ctx. Explicit args win; else the DB meta recorded at split time.

    The DB knows the source doc, tool, and co-author (recorded by split-skill-dag.py),
    so a plain `apply-node.py <id>` picks everything up from the DB. Flags override.
    """
    skill = args.skill
    tool = args.tool
    no_tool = args.no_tool
    coauthor = args.co_author
    if not args.skill:
        skill = meta_row(conn, "source") or None
    if not args.tool and not args.no_tool:
        tool = meta_row(conn, "tool") or None
    if not args.co_author:
        coauthor = meta_row(conn, "coauthor") or None
    if not skill or not os.path.exists(skill):
        sys.exit("no --skill given and DB meta has no source: %s" % db)
    if tool and not os.path.exists(tool):
        sys.exit("tool from DB meta missing: %s (pass --tool PATH or --no-tool)" % tool)
    return Ctx.from_args(argparse.Namespace(
        skill=skill, db=db, tool=tool or None, no_tool=bool(no_tool),
        co_author=coauthor or None, wt_root=args.wt_root, chunk_words=500,
    ))


def main():
    ap = argparse.ArgumentParser()
    Ctx.add_args(ap)
    ap.add_argument("node_id", nargs="?", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--next", nargs="?", const=10, type=int)
    args = ap.parse_args()

    if args.skill:
        db = args.db or default_db_for(os.path.abspath(args.skill))
    else:
        # no --skill: --db alone (or the default path) drives everything from meta
        db = args.db or default_db_for(os.path.expanduser("~/Source/skill-split/SKILL.md"))
    if not os.path.exists(db):
        sys.exit("no DB at %s — run split-skill-dag.py first" % db)
    conn = db_conn(db)
    args.db = db

    if args.status:
        cur = conn.cursor()
        print("nodes:", dict(cur.execute("SELECT status, COUNT(*) FROM nodes GROUP BY status").fetchall()))
        print("tasks:", dict(cur.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()))
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

    ctx = ctx_from_db_meta(conn, db, args)
    base = ctx.base

    cur = conn.cursor()
    node = cur.execute("SELECT * FROM nodes WHERE id=?", (args.node_id,)).fetchone()
    if not node:
        sys.exit("no such node: %d" % args.node_id)
    if node["status"] == "done":
        sys.exit("node %d already done" % args.node_id)

    tasks = cur.execute("SELECT * FROM tasks WHERE node_id=? ORDER BY id", (args.node_id,)).fetchall()
    task_ids = [t["id"] for t in tasks]

    diff_path = os.path.join(base, "diffs", "N%04d.diff" % args.node_id)
    verdicts_path = os.path.join(base, "verdicts", "N%04d.md" % args.node_id)

    ok, detail = apply_diff(ctx, diff_path)
    if not ok:
        print("CONFLICT node %d: %s" % (args.node_id, detail))
        cur.execute("UPDATE nodes SET status='conflict' WHERE id=?", (args.node_id,))
        conn.commit()
        sys.exit(1)

    problems = check_doc_agreement(ctx)
    if problems:
        print("AGREEMENT FAIL node %d:" % args.node_id)
        for p in problems:
            print("  - %s" % p)
        cur.execute("UPDATE nodes SET status='conflict' WHERE id=?", (args.node_id,))
        conn.commit()
        sys.exit(1)

    sha, msg = commit_node(ctx, args.node_id, node["heading"], task_ids, args.dry_run)
    if sha is None and msg.startswith("commit failed"):
        print("COMMIT FAIL node %d: %s" % (args.node_id, msg))
        sys.exit(1)

    if args.dry_run:
        print("DRY-RUN node %d: %s (%s)" % (args.node_id, node["heading"][:50], sha or "keep"))
        return

    verdicts = read_verdicts(verdicts_path)
    mark_done(conn, args.node_id, sha)
    print("node %d done: %s (%s)" % (args.node_id, node["heading"][:50], sha or "keep"))
    if verdicts:
        for tid, (kind, verdict, reason) in sorted(verdicts.items()):
            print("  %s %s: %s — %s" % (tid, kind, verdict, reason[:80]))


if __name__ == "__main__":
    main()
