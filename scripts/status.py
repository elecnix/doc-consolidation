#!/usr/bin/env python3
"""status.py — one-line pipeline progress for the 2-minute monitor tick."""
import os
import sqlite3
import time

db = os.path.expanduser("~/.cache/fleet-skill-split/skill-dag.db")
log = os.path.expanduser("~/.cache/fleet-skill-split/run.log")
conn = sqlite3.connect(db)
nd = conn.execute("SELECT COUNT(*) FROM nodes WHERE status='done'").fetchone()[0]
np = conn.execute("SELECT COUNT(*) FROM nodes WHERE status='pending'").fetchone()[0]
nc = conn.execute("SELECT COUNT(*) FROM nodes WHERE status='conflict'").fetchone()[0]
nt = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
fr = conn.execute("""
    SELECT COUNT(*) FROM nodes n
    WHERE n.status='pending'
      AND NOT EXISTS (SELECT 1 FROM edges e JOIN nodes d ON d.id=e.depends_on
                      WHERE e.node_id=n.id AND d.status!='done')
""").fetchone()[0]
recent = conn.execute(
    "SELECT COUNT(*) FROM tasks WHERE status='done' AND done_at > datetime('now','localtime','-2 minutes')"
).fetchone()[0]
last = ""
if os.path.exists(log):
    lines = [l for l in open(log).read().strip().split("\n") if l.strip()]
    if lines:
        last = lines[-1][:110]
print(f"[{time.strftime('%H:%M:%S')}] done {nd}/216 pending {np} conflict {nc} | "
      f"tasks {nt}/748 (+{recent}/2m) | frontier {fr} | {last}")
