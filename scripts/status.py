#!/usr/bin/env python3
"""status.py — one-line pipeline progress for the 2-minute monitor tick.

Counts are read from the DB, never hardcoded. All paths derive from --skill
(see common.py); --db or the derived default is enough too.

Usage:
  status.py [--skill PATH] [--db PATH] [--log PATH]
"""

import argparse
import os
import sqlite3
import time

from common import Ctx


def main():
    ap = argparse.ArgumentParser()
    Ctx.add_args(ap)
    ap.add_argument("--log", default=None, help="run.log path (default: <db dir>/run.log)")
    args = ap.parse_args()
    ctx = Ctx.from_args(args)
    if not os.path.exists(ctx.db):
        print("[%s] no DB at %s — run split-skill-dag.py first" % (time.strftime('%H:%M:%S'), ctx.db))
        return
    conn = sqlite3.connect(ctx.db)
    nd = conn.execute("SELECT COUNT(*) FROM nodes WHERE status='done'").fetchone()[0]
    np = conn.execute("SELECT COUNT(*) FROM nodes WHERE status='pending'").fetchone()[0]
    nc = conn.execute("SELECT COUNT(*) FROM nodes WHERE status='conflict'").fetchone()[0]
    ntotal = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    nt = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
    nttotal = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    fr = conn.execute("""
        SELECT COUNT(*) FROM nodes n
        WHERE n.status='pending'
          AND NOT EXISTS (SELECT 1 FROM edges e JOIN nodes d ON d.id=e.depends_on
                          WHERE e.node_id=n.id AND d.status!='done')
    """).fetchone()[0]
    recent = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='done' AND done_at > datetime('now','localtime','-2 minutes')"
    ).fetchone()[0]
    log = args.log or os.path.join(ctx.base, "run.log")
    last = ""
    if os.path.exists(log):
        lines = [l for l in open(log).read().strip().split("\n") if l.strip()]
        if lines:
            last = lines[-1][:110]
    print("[%s] done %d/%d pending %d conflict %d | "
          "tasks %d/%d (+%d/2m) | frontier %d | %s" % (
              time.strftime('%H:%M:%S'), nd, ntotal, np, nc, nt, nttotal, recent, fr, last))


if __name__ == "__main__":
    main()
