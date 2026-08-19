#!/usr/bin/env python3
"""
split-skill-dag.py — split a skill markdown file into a DAG of paragraph nodes in sqlite.

Nodes: one per section (heading + body), with oversized sections chunked into
consecutive chunk nodes. Edges: heading hierarchy (child depends on parent), chunk
order, and cross-references (a node that names another node's heading depends on it).

Tasks: per-node verification tasks assigned by content heuristics:
  verify-accuracy        every node
  verify-redundancy      every node
  verify-discoverability nodes that invoke the tool (`<tool> <verb>` / `<tool> note <topic>`)
  verify-consistency     nodes with code blocks or tool names
  verify-currency        nodes with dates / operator rulings

All paths derive from --skill (see common.py): the DB lives at
~/.cache/<skill-name>/skill-dag.db, briefs/diffs/verdicts/proposals next to it,
per-node worktrees at <wt-root>/<skill-name>/N%04d.

Usage:
  split-skill-dag.py [--skill PATH] [--db PATH] [--chunk-words N] [--force]
                     [--briefs-only] [--next N] [--tool PATH] [--no-tool]
                     [--co-author NAME] [--wt-root DIR]

The DB is written fresh; --force overwrites an existing one (which would clobber
recorded task progress — use it only to rebuild from scratch).
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time

from common import Ctx

# --------------------------------------------------------------------------- task catalog

def make_catalog(tool_name):
    """Task catalog with the tool's name substituted into the descriptions.

    {disp} is a prose reference ("the tool"); {cmd} a command invocation
    (the detected tool name, or "<tool>" when none is detected). Both render
    naturally with or without a detected tool.
    """
    disp = "the"
    cmd = tool_name or "<tool>"
    return {
        "verify-accuracy": {
            "title": "Verify this paragraph is still accurate",
            "description": (
                "Check every claim in this paragraph against the current state of {disp} "
                "tool and the skill repo. A claim is inaccurate if the tool's behavior, "
                "command names, defaults, env vars, file paths, or flags have changed since "
                "the paragraph was written. How to check: read the paragraph, then read the "
                "relevant part of {disp} script (grep for the name or command it mentions) "
                "and run `{cmd} --help` (or read the script's header comment) where the "
                "paragraph names a command. Fix drift by editing the paragraph; if the "
                "paragraph describes behavior that no longer exists, propose deletion. Do "
                "not edit {disp} script — if the tool itself is wrong, write a move-to-tool "
                "proposal instead."
            ).format(disp=disp, cmd=cmd),
        },
        "verify-redundancy": {
            "title": "Verify this paragraph is redundant",
            "description": (
                "The goal is to shrink SKILL.md a LOT. A paragraph is redundant if its "
                "content is already discoverable by normal use of the tool: `{cmd} --help` "
                "output, the script's header comment, its code comments, or the tool's "
                "stdout. If the orchestrator would learn it by running the tool or reading "
                "its source/--help, the paragraph should be trimmed to a one-line pointer or "
                "deleted. Keep only what is needed BEFORE any tool runs: how to decide, how "
                "to report, what to delegate, and the failure modes of the process itself. "
                "How to check: run the command the paragraph describes and compare what it "
                "prints with what the paragraph says. Verdict options: keep / trim-to-pointer "
                "/ delete / move-to-tool (write a proposal; do not edit {disp} script)."
            ).format(disp=disp, cmd=cmd),
        },
        "verify-discoverability": {
            "title": "Verify the content is discoverable from the tool",
            "description": (
                "For every `{cmd} <subcommand>` invocation or tool behavior this paragraph "
                "describes, run `{cmd} --help` (and the subcommand itself where safe, e.g. "
                "--dry-run) and confirm the paragraph adds information beyond what the tool "
                "prints. Anything the tool already says should be removed from the paragraph. "
                "If the tool does NOT say it, the paragraph may stay — or the rule should be "
                "embedded in the tool (write a move-to-tool proposal; do not edit {disp} "
                "script yourself). The test: would the orchestrator find this out by normal "
                "usage of the tool? If yes, the paragraph is discoverable and should shrink "
                "to a pointer."
            ).format(disp=disp, cmd=cmd),
        },
        "verify-consistency": {
            "title": "Verify the paragraph agrees with the script",
            "description": (
                "Cross-check names, defaults, and mechanics against the tool script: command "
                "names, flags, env vars, file paths, defaults, exit codes. The doc and the "
                "script must agree. How to check: grep the tool script for each name the "
                "paragraph uses; run the tool's self-check where one exists. Fix any drift in "
                "the paragraph. Do not edit the tool script — if the script is wrong, write "
                "a move-to-tool proposal."
            ),
        },
        "verify-currency": {
            "title": "Verify the paragraph is current",
            "description": (
                "Check for stale dates, superseded operator rulings, references to changes "
                "that have since landed or been reverted, and 'recently' claims. A paragraph "
                "that records a ruling must keep the ruling's date and must not be "
                "contradicted by a later ruling in the same file. How to check: read the "
                "paragraph for dates and ruling markers (e.g. 'operator ruling', 'REVISED', "
                "'adopted'), then search the rest of the doc and `git log` for later rulings "
                "that supersede it. Fix by updating the paragraph or proposing deletion."
            ),
        },
    }


# --------------------------------------------------------------------------- parsing

def parse_skill(path):
    """Return (frontmatter dict, lines list, body_start_line_index)."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    fm = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        j = 1
        while j < len(lines) and lines[j].strip() != "---":
            if ":" in lines[j]:
                k, v = lines[j].split(":", 1)
                fm[k.strip()] = v.strip()
            j += 1
        body_start = j + 1
    return fm, lines, body_start


def build_sections(lines, body_start):
    """Return a list of section dicts: heading, level, start_line (1-based), body (list of lines)."""
    sections = []
    cur = None
    infence = False
    for idx in range(body_start, len(lines)):
        l = lines[idx]
        if l.startswith("```"):
            infence = not infence
        if not infence:
            m = re.match(r"^(#{1,6}) (.+)$", l)
            if m:
                if cur:
                    sections.append(cur)
                cur = {"heading": m.group(2), "level": len(m.group(1)),
                       "start_line": idx + 1, "body": []}
                continue
        if cur is not None:
            cur["body"].append(l)
    if cur:
        sections.append(cur)
    for s in sections:
        s["end_line"] = s["start_line"] + len(s["body"])
    return sections


def split_blocks(body):
    """Split body lines into blocks at blank lines; blank lines attach to the preceding block."""
    blocks = []
    cur = []
    for l in body:
        cur.append(l)
        if not l.strip():
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def chunk_body(body, chunk_words):
    """Chunk body lines into consecutive chunks, each at most chunk_words words (block-granular)."""
    blocks = split_blocks(body)
    chunks = []
    cur = []
    cur_words = 0
    for b in blocks:
        w = sum(len(x.split()) for x in b)
        if cur and cur_words + w > chunk_words:
            chunks.append(cur)
            cur = []
            cur_words = 0
        cur.extend(b)
        cur_words += w
    if cur:
        chunks.append(cur)
    return chunks


def words_of(lines):
    return sum(len(l.split()) for l in lines)


# --------------------------------------------------------------------------- node building

def build_nodes(sections, chunk_words):
    """Return list of node dicts with ids assigned in document order."""
    nodes = []
    for s in sections:
        total = words_of(s["body"])
        if total <= chunk_words:
            nodes.append({
                "kind": "section", "heading": s["heading"], "level": s["level"],
                "chunk": 0, "total_chunks": 1,
                "start_line": s["start_line"], "end_line": s["end_line"],
                "word_count": total, "content": "\n".join(s["body"]),
            })
        else:
            chunks = chunk_body(s["body"], chunk_words)
            offset = 0
            for k, c in enumerate(chunks):
                start = s["start_line"] + 1 + offset
                end = start + len(c) - 1
                nodes.append({
                    "kind": "chunk", "heading": s["heading"], "level": s["level"],
                    "chunk": k, "total_chunks": len(chunks),
                    "start_line": start, "end_line": end,
                    "word_count": words_of(c), "content": "\n".join(c),
                })
                offset += len(c)
    for i, n in enumerate(nodes):
        n["id"] = i + 1  # node 0 is the frontmatter node
    return nodes


def build_edges(nodes):
    """Return list of (node_id, depends_on, kind) edges: hierarchy, chunk order, xrefs."""
    edges = []
    seen = set()

    def add(a, b, kind):
        if a == b or (a, b) in seen:
            return
        seen.add((a, b))
        edges.append((a, b, kind))

    # hierarchy: nearest previous node with a strictly lower heading level
    for i, n in enumerate(nodes):
        if n["kind"] == "chunk" and n["chunk"] > 0:
            continue  # chunk order handled below; hierarchy parent comes from chunk 0
        for j in range(i - 1, -1, -1):
            p = nodes[j]
            if p["level"] < n["level"]:
                add(n["id"], p["id"], "hierarchy")
                break

    # chunk order: chunk k+1 depends on chunk k
    for i, n in enumerate(nodes):
        if n["kind"] == "chunk" and n["chunk"] > 0:
            prev = nodes[i - 1]
            if prev["heading"] == n["heading"] and prev["chunk"] == n["chunk"] - 1:
                add(n["id"], prev["id"], "chunk")

    # cross-references: content names another node's heading
    for n in nodes:
        for m in nodes:
            if n["id"] == m["id"] or len(m["heading"]) < 5:
                continue
            if m["heading"] in n["content"]:
                add(n["id"], m["id"], "xref")
    return edges


# --------------------------------------------------------------------------- task assignment

def assign_tasks(nodes, fm, tool_name):
    """Return list of task dicts: id, node_id, kind, title, description."""
    tasks = []
    seq = 0
    catalog = make_catalog(tool_name)
    all_nodes = [{"id": 0, "heading": "frontmatter", "content": fm.get("description", "")}] + nodes
    for n in all_nodes:
        content = n["content"]
        kinds = ["verify-accuracy", "verify-redundancy"]
        if tool_name and re.search(r"\b%s (note|[a-z][a-z-]+)\b" % re.escape(tool_name), content):
            kinds.append("verify-discoverability")
        if "```" in content or (tool_name and re.search(
                r"\b(%s|note|_DIR|~/.cache)\b" % re.escape(tool_name), content)):
            kinds.append("verify-consistency")
        if re.search(r"\d{4}-\d\d-\d\d|operator ruling|REVISED|adopted", content):
            kinds.append("verify-currency")
        for k in kinds:
            seq += 1
            tasks.append({
                "id": "T%04d" % seq,
                "node_id": n["id"],
                "kind": k,
                "title": catalog[k]["title"],
                "description": catalog[k]["description"],
            })
    return tasks


# --------------------------------------------------------------------------- db

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  heading TEXT,
  level INTEGER,
  chunk INTEGER DEFAULT 0,
  total_chunks INTEGER DEFAULT 1,
  start_line INTEGER,
  end_line INTEGER,
  word_count INTEGER,
  content TEXT,
  status TEXT DEFAULT 'pending',
  verdict TEXT,
  notes TEXT
);
CREATE TABLE edges (
  node_id INTEGER NOT NULL,
  depends_on INTEGER NOT NULL,
  kind TEXT,
  PRIMARY KEY (node_id, depends_on)
);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  node_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  commit_id TEXT,
  done_at TEXT,
  subagent TEXT
);
CREATE INDEX idx_tasks_node ON tasks(node_id);
CREATE INDEX idx_edges_dep ON edges(depends_on);
"""


def write_db(ctx, fm, nodes, edges, tasks):
    db_path = ctx.db
    force = getattr(ctx, "force", False)
    if os.path.exists(db_path) and not force:
        sys.exit("DB exists: %s — pass --force to rebuild (clobbers recorded progress)" % db_path)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    cur = conn.cursor()
    commit = ""
    if ctx.repo_root:
        commit = subprocess.run(
            ["git", "-C", ctx.repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
    cur.executemany("INSERT INTO meta VALUES (?,?)", [
        ("source", ctx.skill_path),
        ("source_commit", commit),
        ("split_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("frontmatter_name", fm.get("name", "")),
        ("frontmatter_description", fm.get("description", "")),
        ("tool", ctx.tool or ""),
        ("coauthor", ctx.coauthor or ""),
        ("repo_root", ctx.repo_root or ""),
        ("skill_rel", ctx.skill_rel),
        ("wt_dir", ctx.wt_dir),
        ("branch_prefix", ctx.branch_prefix),
    ])
    # frontmatter node (id 0)
    cur.execute(
        "INSERT INTO nodes (id, kind, heading, level, chunk, total_chunks, start_line, end_line, word_count, content) "
        "VALUES (0,'frontmatter','frontmatter',0,0,1,1,4,?,?)",
        (words_of([fm.get("description", "")]), fm.get("description", "")))
    cur.executemany(
        "INSERT INTO nodes (id, kind, heading, level, chunk, total_chunks, start_line, end_line, word_count, content) "
        "VALUES (:id,:kind,:heading,:level,:chunk,:total_chunks,:start_line,:end_line,:word_count,:content)",
        nodes)
    cur.executemany("INSERT INTO edges (node_id, depends_on, kind) VALUES (?,?,?)", edges)
    cur.executemany(
        "INSERT INTO tasks (id, node_id, kind, title, description) VALUES (:id,:node_id,:kind,:title,:description)",
        tasks)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- briefs

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "node"


def write_briefs(ctx, nodes, tasks, fm):
    os.makedirs(ctx.briefs_dir, exist_ok=True)
    by_node = {}
    for t in tasks:
        by_node.setdefault(t["node_id"], []).append(t)
    all_nodes = [{"id": 0, "kind": "frontmatter", "heading": "frontmatter", "level": 0,
                  "chunk": 0, "total_chunks": 1, "start_line": 1, "end_line": 4,
                  "word_count": words_of([fm.get("description", "")]),
                  "content": fm.get("description", "")}] + nodes
    for n in all_nodes:
        ntasks = by_node.get(n["id"], [])
        fname = "N%04d-%s.md" % (n["id"], slugify(n["heading"]))
        with open(os.path.join(ctx.briefs_dir, fname), "w", encoding="utf-8") as f:
            f.write(brief_text(ctx, n, ntasks, all_nodes))
    return len(all_nodes)


def heading_path(nodes, n):
    """Return the heading ancestry of node n as a list of (level, heading)."""
    path = []
    for m in nodes:
        if m["id"] == n["id"]:
            break
        if m["level"] < n["level"]:
            path = [x for x in path if x[0] < m["level"]]
            path.append((m["level"], m["heading"]))
    return path


def brief_text(ctx, n, ntasks, nodes):
    loc = "lines %d-%d" % (n["start_line"], n["end_line"]) if n["kind"] != "frontmatter" else "frontmatter"
    path = heading_path(nodes, n)
    path_str = " > ".join(h for _, h in path) if path else "(top level)"
    wt = ctx.worktree_path(n["id"])
    task_lines = "\n".join(
        "- **%s** — %s\n  %s" % (t["id"], t["title"], t["description"]) for t in ntasks
    )
    tool = ctx.tool or "(no tool detected)"
    return f"""# Node {n['id']} — {n['heading'] or '(frontmatter)'}

## Your tasks (answer ALL of them; one subagent per node because they all edit the same lines)
{task_lines}

## The paragraph (node {n['id']}, {n['kind']}, {loc}, {n['word_count']} words)
Heading path: {path_str}

```
{n['content']}
```

## Your worktree
- Your worktree: {wt} (branch {ctx.worktree_branch(n['id'])}), created from the current
  split-branch HEAD before you started. The skill file in YOUR worktree is
  {ctx.worktree_path(n['id'])}/{ctx.skill_name} — that is the file you edit.
- Locate your section by its heading — the line numbers in this brief are from the
  original file and may have shifted after earlier trims.

## Context you need
- The tool (the checked implementation of the recipes): {tool}
- The goal: shrink {ctx.skill_name} a LOT. Embed its rules into the tool CLI as much as
  possible, so the doc does not have to say something the orchestrator (the one USING
  the doc and the tool) will find out by reading the output or --help of the tool.
  Everything should be discoverable, in the expected location, discovered by normal usage.
- **Trimming policy (strict):** only trim text that is VERBATIM restated in the tool's
  header, a code comment, or `--help`/note output. KEEP every decision rule, every
  failure-mode illustration, and every `never`/`always` rule even if it is partially
  discoverable — partial discoverability is NOT enough to delete. When in doubt, keep.

## How to investigate
1. Read the paragraph above.
2. Search your worktree's {ctx.skill_name} for the surrounding section (grep for the
   heading) so you understand context.
3. For every `{tool_name_for(ctx)} <subcommand>` / tool invocation the paragraph names,
   run: `{ctx.tool or tool_name_for(ctx)} --help` and read the script's header comment
   (and run the subcommand itself where safe, e.g. with --dry-run). Compare what the
   tool prints with what the paragraph claims.
4. Grep the tool script for any name, default, env var, or path the paragraph mentions
   and check it still matches.
5. Check `git log --oneline -10` in the doc's repo ({ctx.repo_root or 'not a git repo'})
   for recent changes that may have superseded the paragraph.

## Verdict options (one per task)
- **keep** — the paragraph is accurate, non-redundant, and needed before the tool runs.
- **trim-to-pointer** — the trimmed text is VERBATIM restated in the tool's header / a
  code comment / `--help` / a note; keep one line that names the finding and points at
  the verb/note/script. If it is only partially discoverable, keep it.
- **delete** — fully redundant or superseded; nothing of value remains.
- **move-to-tool** — the rule belongs embedded in the tool (a new verb, a note body,
  a doc string). Write a proposal file (see below); do NOT edit the tool script
  yourself.

## What to produce
1. Edit {ctx.worktree_path(n['id'])}/{ctx.skill_name} — only your section's lines
   (keep = no edit).
2. Produce the diff: `git -C {ctx.worktree_path(n['id'])} diff --no-prefix -- {ctx.skill_name} > {os.path.join(ctx.diffs_dir, 'N%04d.diff' % n['id'])}`
   (an empty file means keep — no change).
3. Write one verdict line per task to: {os.path.join(ctx.verdicts_dir, 'N%04d.md' % n['id'])}
   Format, one line per task:
   `T0001 verify-accuracy: keep — one-line reason, citing script:line for each claim verified`
   Every verdict MUST cite the specific script filename and line numbers you checked
   (e.g. `run-pr-gates.sh:388-389` or `open-pr.sh:7-10,72-74`). A citation is what makes
   the verdict auditable and is what the orchestrator writes into the commit body.
4. If any verdict is move-to-tool, write a proposal to:
   {os.path.join(ctx.proposals_dir, 'N%04d.md' % n['id'])} — one paragraph per rule: what
   rule, which verb/note should carry it, and why the tool is the right home.
5. Report back: your verdicts, a one-line reason each, and the diff path.

## Rules
- Edit ONLY your worktree's {ctx.skill_name}. Never touch the shared split worktree
  ({ctx.skill_path}), never commit, never push.
- Touch only your section's lines — other sections belong to other subagents running
  in parallel.
- Never edit the tool script. Never paste secrets or internal identifiers into the diff
  or proposal.
"""


def tool_name_for(ctx):
    return ctx.tool_name or "tool"


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Split a skill markdown file into a DAG of paragraph nodes in sqlite.")
    Ctx.add_args(ap)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--briefs-only", action="store_true", help="regenerate briefs from the current skill without touching the DB")
    ap.add_argument("--next", type=int, default=0, help="print the next N schedulable nodes and exit")
    args = ap.parse_args()
    ctx = Ctx.from_args(args)
    ctx.force = args.force
    ctx.ensure_dirs()

    fm, lines, body_start = parse_skill(ctx.skill_path)
    sections = build_sections(lines, body_start)
    nodes = build_nodes(sections, ctx.chunk_words)
    edges = build_edges(nodes)
    tasks = assign_tasks(nodes, fm, ctx.tool_name)

    # snapshot the pristine original for subagent diffing
    with open(ctx.orig_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    if args.briefs_only:
        n_briefs = write_briefs(ctx, nodes, tasks, fm)
        print("briefs regenerated: %d -> %s" % (n_briefs, ctx.briefs_dir))
        return

    write_db(ctx, fm, nodes, edges, tasks)
    n_briefs = write_briefs(ctx, nodes, tasks, fm)

    # summary
    from collections import Counter
    kinds = Counter(t["kind"] for t in tasks)
    n_edges = len(edges)
    n_chunks = sum(1 for n in nodes if n["kind"] == "chunk")
    print("split %s -> %s" % (ctx.skill_path, ctx.db))
    print("  nodes: %d (%d sections, %d chunks, 1 frontmatter)" % (len(nodes), len(nodes) - n_chunks - 1, n_chunks))
    print("  edges: %d (%s)" % (n_edges, ", ".join("%s=%d" % (k, c) for k, c in Counter(e[2] for e in edges).items())))
    print("  tasks: %d (%s)" % (len(tasks), ", ".join("%s=%d" % (k, c) for k, c in kinds.items())))
    print("  briefs: %d -> %s" % (n_briefs, ctx.briefs_dir))
    print("  original snapshot: %s" % ctx.orig_path)
    print("  tool: %s" % (ctx.tool or "(none)"))

    if args.next:
        conn = sqlite3.connect(ctx.db)
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT n.id, n.heading, n.kind, n.word_count, n.start_line, n.end_line
            FROM nodes n
            WHERE n.status = 'pending'
              AND NOT EXISTS (
                SELECT 1 FROM edges e
                JOIN nodes d ON d.id = e.depends_on
                WHERE e.node_id = n.id AND d.status != 'done'
              )
            ORDER BY n.id
            LIMIT ?
        """, (args.next,)).fetchall()
        for r in rows:
            print("  next: node %d %s (%s, %dw, lines %d-%d)" % (r[0], r[1], r[2], r[3], r[4], r[5]))
        conn.close()


if __name__ == "__main__":
    main()
