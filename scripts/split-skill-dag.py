#!/usr/bin/env python3
"""
split-skill-dag.py — split a skill markdown file into a DAG of paragraph nodes in sqlite.

Nodes: one per section (heading + body), with oversized sections chunked into
consecutive chunk nodes, plus node 0 for the YAML frontmatter when the doc has any.
The node ranges are a partition: every vale alert lands in exactly one node. Edges: heading hierarchy (child depends on parent), chunk
order, and cross-references (a node that names another node's heading depends on it).

Tasks: per-node verification tasks assigned by the selected modules, then by content
heuristics. The module gate comes first: a deslop-only run must never hand a worker a
trim task, whose text orders it to shrink the paragraph and propose deletion.
  verify-accuracy        every node, under trim / progressive-disclosure
  verify-redundancy      every node, under trim / progressive-disclosure
  deslop-prose           every node, under deslop
  verify-discoverability nodes that invoke the tool (`<tool> <verb>` / `<tool> note <topic>`)
  verify-consistency     nodes with code blocks; also on a tool name when a tool exists
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
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

from common import Ctx, resolve_style_docs
from prompt_modules import DEFAULT_MODULES, normalize as normalize_modules
from prompt_modules import policy_text, verdict_options_text

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
                "not edit {disp} script — if the tool itself is wrong, write a progressive-disclosure "
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
                "/ delete / progressive-disclosure (write a proposal; do not edit {disp} script)."
            ).format(disp=disp, cmd=cmd),
        },
        "verify-discoverability": {
            "title": "Verify the content is discoverable from the tool",
            "description": (
                "For every `{cmd} <subcommand>` invocation or tool behavior this paragraph "
                "describes, run `{cmd} --help` (and the subcommand itself where safe, e.g. "
                "--dry-run) and confirm the paragraph adds information beyond what the tool "
                "prints. Anything the tool already says should be removed from the paragraph "
                "(trim-to-pointer). If the tool does NOT say it, ask whether the rule is ABOUT "
                "that script's behavior: if yes, it is a progressive-disclosure proposal — the rule "
                "belongs embedded in the script's header/--help/code-comments so the script "
                "self-describes it (write a proposal; do not edit {disp} script yourself). If "
                "the rule is genuine cross-script orchestration no single script can "
                "self-describe, keep it. The test: would the orchestrator find this out by "
                "normal usage of the tool? If yes, the paragraph is discoverable and should "
                "shrink to a pointer."
            ).format(disp=disp, cmd=cmd),
        },
        "deslop-prose": {
            "title": "Reword the prose vale flagged as machine-written",
            "description": (
                "Answer every vale alert your brief lists for this paragraph's lines, and "
                "no others. An alert is a signal, not an order: act on one only where the "
                "sentence is wrong beyond the alert (it hides the main point, buries two "
                "ideas, goes passive), and refuse it where the phrasing is the house voice "
                "or a named term. Facts, numbers, filenames, quoted material, and code "
                "identifiers stay exactly as they are — this task changes wording only, "
                "and the paragraph's word count is not expected to move. How to check: "
                "measure the FRAGMENT, not the line range. Copy the paragraph's before "
                "text and its after text into two `.md` files, run vale on each (the "
                "recipe is in the deslop policy below), and report the rule-name diff plus "
                "the `vale N->M` pair. Verdict options: keep / deslop / slop_refused."
            ),
        },
        "verify-consistency": {
            "title": "Verify the paragraph agrees with the script" if tool_name else
                     "Verify the paragraph agrees with the code it describes",
            "description": (
                "Cross-check names, defaults, and mechanics against the tool script: command "
                "names, flags, env vars, file paths, defaults, exit codes. The doc and the "
                "script must agree. How to check: grep the tool script for each name the "
                "paragraph uses; run the tool's self-check where one exists. Fix any drift in "
                "the paragraph. Do not edit the tool script — if the script is wrong, write "
                "a progressive-disclosure proposal."
            ) if tool_name else (
                "Cross-check every command name, flag, env var, file path, default, and exit "
                "code this paragraph states against the file that defines it. This doc has no "
                "single tool script, so the check runs against whatever the paragraph names: "
                "grep the repo for each name, open the file it lives in, and compare. Also "
                "check the paragraph against the rest of this doc — the two must not "
                "contradict each other. Fix any drift in the paragraph. Never edit the code "
                "the paragraph describes; if the code is what is wrong, say so in the verdict "
                "and leave it."
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
    """Return (frontmatter dict, lines list, body_start_line_index).

    body_start is 0 when the doc opens with no YAML fence, and 0 again when it opens with
    one that is never closed -- an unclosed fence is not frontmatter, and treating it as
    one swallowed the whole file. Otherwise body_start is the 0-based index of the first
    body line, which is also the 1-based line number of the CLOSING fence: the frontmatter
    node's end_line.
    """
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
        if j < len(lines):          # closing fence found
            body_start = j + 1
        else:
            fm = {}                 # unclosed fence: the doc has no frontmatter
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


def frontmatter_node(fm, fm_end):
    """Node 0, or None when the doc opens with no YAML fence.

    It used to be inserted unconditionally with a hardcoded range of 1-4. On a doc whose
    first line is a heading that phantom range overlapped the first real nodes, so the same
    vale alert landed in two briefs and two workers were each told they owned the line. On
    a doc whose frontmatter runs past line 4 the remaining lines fell in no node's range.
    Both directions are gone: the node exists only when the frontmatter does, and its
    end_line is the closing fence.
    """
    if not fm_end:
        return None
    desc = fm.get("description", "")
    return {"id": 0, "kind": "frontmatter", "heading": "frontmatter", "level": 0,
            "chunk": 0, "total_chunks": 1, "start_line": 1, "end_line": fm_end,
            "word_count": words_of([desc]), "content": desc}


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

def assign_tasks(nodes, fm_node, tool_name, modules):
    """Return list of task dicts: id, node_id, kind, title, description.

    The task kinds are gated on the selected modules. A trim task tells the worker to
    shrink the paragraph and propose deletion, so a deslop-only run (which changes
    wording and never facts) must not be handed one.
    """
    tasks = []
    seq = 0
    catalog = make_catalog(tool_name)
    all_nodes = ([fm_node] if fm_node else []) + nodes
    for n in all_nodes:
        content = n["content"]
        kinds = []
        if "trim" in modules or "progressive-disclosure" in modules:
            kinds += ["verify-accuracy", "verify-redundancy"]
        if "deslop" in modules:
            kinds.append("deslop-prose")
        if tool_name and re.search(r"\b%s (note|[a-z][a-z-]+)\b" % re.escape(tool_name), content):
            kinds.append("verify-discoverability")
        # A code block is checkable with or without a tool: the names in it still have to
        # match the files that define them. Requiring a tool dropped the check entirely on
        # a tool-free doc, where the task was answerable, just not by grepping "the tool".
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


def write_db(ctx, fm, fm_node, nodes, edges, tasks):
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
    meta_rows = [
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
        ("modules", ",".join(ctx.modules)),
    ]
    # style_docs belongs to the deslop module alone; a trim-only DB records no row.
    if "deslop" in ctx.modules:
        meta_rows.append(("style_docs", ",".join(ctx.style_docs)))
    cur.executemany("INSERT INTO meta VALUES (?,?)", meta_rows)
    # frontmatter node (id 0) -- only when the doc actually has frontmatter
    if fm_node:
        cur.execute(
            "INSERT INTO nodes (id, kind, heading, level, chunk, total_chunks, start_line, end_line, word_count, content) "
            "VALUES (:id,:kind,:heading,:level,:chunk,:total_chunks,:start_line,:end_line,:word_count,:content)",
            fm_node)
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


# --------------------------------------------------------------------------- vale alerts

def run_vale(path):
    """Return vale's alerts for one file as a flat list, or None when it could not run.

    None means "no measurement" (binary missing, no JSON, unparsable) and the brief says
    so; an empty list means vale ran and found nothing, which is itself suspect. Never
    pass --glob with an explicit file path: --glob is a directory-walk filter, and on a
    path it does not match vale prints `{}` and exits 0. The file must end in .md.

    vale exits 1 whenever it finds alerts, so the status is never checked -- the JSON is
    the only signal, matching resolve_tool()'s treatment of an optional dependency.
    """
    try:
        r = subprocess.run(["vale", "--output=JSON", path], capture_output=True, text=True)
    except OSError:
        return None
    out = (r.stdout or "").strip()
    if not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    alerts = []
    for v in data.values():
        if isinstance(v, list):
            alerts.extend(a for a in v if isinstance(a, dict))
    return alerts


def alerts_in_range(alerts, lo, hi):
    """The alerts anchored inside [lo, hi], ordered by line then column. None passes through.

    Line is the ABSOLUTE line in the file and, for a block-level rule, the FIRST line of a
    match that can run past hi. Anchoring on it is what keeps the slices a true partition:
    every alert lands in exactly one node.
    """
    if alerts is None:
        return None
    out = [a for a in alerts if lo <= (a.get("Line") or 0) <= hi]
    return sorted(out, key=lambda a: ((a.get("Line") or 0), ((a.get("Span") or [0])[0])))


def render_alerts(alerts, lo, hi):
    """The brief's "Vale alerts in your line range" body."""
    if alerts is None:
        return ("vale produced no measurement for this run (the binary is missing, or it\n"
                "printed no JSON). That is a CONTROL FAILURE, not a clean doc: report it and\n"
                "stop, rather than inventing your own slop judgement over this paragraph.")
    head = ("Every alert vale raised inside lines %d-%d of the original file. Answer these and\n"
            "ONLY these. Vale skips code fences and masks inline `code` spans with `*` of the\n"
            "same length, so a match can read as asterisks -- find the text by line and column\n"
            "in your worktree's copy, never by grepping the match string. A block-level alert\n"
            "is anchored at its FIRST line, so its match can run past your range." % (lo, hi))
    if not alerts:
        return head + "\n\n(no alerts in this range: the verdict is `keep`, and say the range was clean.)"
    rows = []
    for a in alerts:
        match = (a.get("Match") or "").replace("\n", " / ")
        if len(match) > 120:
            match = match[:117] + "..."
        span = a.get("Span") or [0, 0]
        rows.append('- L%s c%s %s -- match: "%s"\n  %s'
                    % (a.get("Line"), span[0], a.get("Check"), match, a.get("Message") or ""))
    return head + "\n\n" + "\n".join(rows)


# --------------------------------------------------------------------------- briefs

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "node"


def write_briefs(ctx, nodes, tasks, fm_node):
    os.makedirs(ctx.briefs_dir, exist_ok=True)
    by_node = {}
    for t in tasks:
        by_node.setdefault(t["node_id"], []).append(t)
    all_nodes = ([fm_node] if fm_node else []) + nodes
    # One vale run per doc, over the snapshot whose coordinates the node ranges were
    # computed from. Only the deslop module reads them, so nothing else pays for it.
    alerts = None
    if "deslop" in ctx.modules:
        alerts = run_vale(ctx.orig_path)
        report_vale(alerts, all_nodes, ctx.orig_path)
    for n in all_nodes:
        ntasks = by_node.get(n["id"], [])
        fname = "N%04d-%s.md" % (n["id"], slugify(n["heading"]))
        node_alerts = alerts_in_range(alerts, n["start_line"], n["end_line"])
        with open(os.path.join(ctx.briefs_dir, fname), "w", encoding="utf-8") as f:
            f.write(brief_text(ctx, n, ntasks, all_nodes, node_alerts))
    return len(all_nodes)


def report_vale(alerts, all_nodes, path):
    """Print what vale measured, in both directions: unassigned AND double-assigned.

    Two spans belong to no node -- the heading line of a chunked section, and everything
    before the first heading -- so an unreported bucket would drop those alerts silently.
    Counting distinct alerts alone was structurally unable to see the opposite failure: it
    read a doc whose node ranges overlapped as "0 in no node's range" while two briefs
    carried the same alert. Summing per-node assignments makes the overlap fail the
    control, because the module's whole partition rests on one alert reaching one worker.
    """
    if alerts is None:
        print("  vale: no measurement (binary missing or no JSON) -- every brief carries a "
              "CONTROL FAILURE note instead of an alert list")
        return
    covered = set()
    assignments = 0
    for n in all_nodes:
        rng = range(n["start_line"], n["end_line"] + 1)
        covered.update(rng)
        assignments += sum(1 for a in alerts if (a.get("Line") or 0) in rng)
    assigned = sum(1 for a in alerts if (a.get("Line") or 0) in covered)
    print("  vale: %d alerts on %s (%d assigned to a node, %d in no node's range)"
          % (len(alerts), os.path.basename(path), assigned, len(alerts) - assigned))
    if assignments != assigned:
        print("  vale: PARTITION BROKEN -- %d assignments for %d assigned alerts. %d alert(s) "
              "fall in more than one node's range, so two workers are each told they own the "
              "same line." % (assignments, assigned, assignments - assigned))
    if not alerts:
        print("  vale: ZERO alerts on the whole doc -- a control failure until proved "
              "otherwise (a non-.md name, or --glob with a file path, prints {} and exits 0)")


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


def brief_text(ctx, n, ntasks, nodes, node_alerts=None):
    loc = "lines %d-%d" % (n["start_line"], n["end_line"]) if n["kind"] != "frontmatter" else "frontmatter"
    path = heading_path(nodes, n)
    path_str = " > ".join(h for _, h in path) if path else "(top level)"
    wt = ctx.worktree_path(n["id"])
    task_lines = "\n".join(
        "- **%s** — %s\n  %s" % (t["id"], t["title"], t["description"]) for t in ntasks
    )
    modules_label = ", ".join(ctx.modules)
    policy = policy_text(ctx.modules, tool=bool(ctx.tool))
    verdict_options = verdict_options_text(ctx.modules, tool=bool(ctx.tool))
    has_tool = bool(ctx.tool)
    deslop = "deslop" in ctx.modules
    trimming = "trim" in ctx.modules or "progressive-disclosure" in ctx.modules
    # A deslop-only run trims nothing, so the trim lane's drift warning does not apply.
    shift_note = ("the line numbers in this brief are from the\n"
                  "  original file and may have shifted after earlier trims." if trimming else
                  "the line numbers in this brief are from the\n"
                  "  original file, and a reword above you can still move them.")

    # Context: every tool-facing line is dropped when there is no tool, where it used to
    # render as literal nonsense ("The tool: (no tool detected)", then "embed its rules
    # into the tool CLI").
    context_lines = []
    if has_tool:
        context_lines.append(
            "- The tool (the checked implementation of the recipes): %s" % ctx.tool)
    if trimming and has_tool:
        context_lines.append(
            "- The goal: shrink %s a LOT. Embed its rules into the tool CLI as much as\n"
            "  possible, so the doc does not have to say something the orchestrator (the one\n"
            "  USING the doc and the tool) will find out by reading the output or --help of\n"
            "  the tool. Everything should be discoverable, in the expected location,\n"
            "  discovered by normal usage." % ctx.skill_name)
    elif trimming:
        context_lines.append(
            "- The goal: shrink %s a LOT. This doc has no tool script, so no rule moves into\n"
            "  a CLI: cut what the doc restates about itself, and keep what a reader needs\n"
            "  before acting." % ctx.skill_name)
    if deslop:
        context_lines.append(
            "- The deslop goal: reword the prose to the house standard. The facts, the\n"
            "  numbers, and the size of this paragraph stay as they are.")
    context_block = "\n".join(context_lines) or "- (no tool detected; the goal is the selected module's own)"

    steps = [
        "Read the paragraph above.",
        "Search your worktree's %s for the surrounding section (grep for the heading) so "
        "you understand context." % ctx.skill_name,
    ]
    if has_tool:
        steps.append(
            "For every `%s <subcommand>` / tool invocation the paragraph names, run "
            "`%s --help` and read the script's header comment (and run the subcommand "
            "itself where safe, e.g. with --dry-run). Compare what the tool prints with "
            "what the paragraph claims." % (tool_name_for(ctx), ctx.tool))
        steps.append(
            "Grep the tool script for any name, default, env var, or path the paragraph "
            "mentions and check it still matches.")
    if deslop:
        steps.append(
            "Work the \"Vale alerts in your line range\" section above, one alert at a time. "
            "Then measure the fragment: copy this paragraph's before text and its after "
            "text into two `.md` files and run vale on each, per the recipe in the policy.")
    steps.append(
        "Check `git log --oneline -10` in the doc's repo (%s) for recent changes that may "
        "have superseded the paragraph." % (ctx.repo_root or "not a git repo"))
    investigate = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(steps))

    alerts_block = ""
    if deslop:
        alerts_block = "\n## Vale alerts in your line range\n%s\n" % render_alerts(
            node_alerts, n["start_line"], n["end_line"])

    style_block = ""
    if deslop:
        if ctx.style_docs:
            style_block = (
                "\n## Writing standard (read it before you reword anything)\n"
                + "\n".join("- %s" % p for p in ctx.style_docs)
                + "\nOpen it and hold its rules while you edit. It comes before every vale rule.\n")
        else:
            style_block = (
                "\n## Writing standard\n"
                "NONE for this run: no --style-doc was passed and the doc's repo has no\n"
                "AGENTS.md or CLAUDE.md. Refuse EVERY punctuation, phrasing and naming alert\n"
                "and report them unacted -- do not guess the house voice.\n")

    ex_id = ntasks[0]["id"] if ntasks else "T0001"
    ex_kind = ntasks[0]["kind"] if ntasks else "verify-accuracy"
    cite_lines = []
    if has_tool:
        cite_lines.append(
            "   A verdict about a CLAIM must cite the specific script filename and line\n"
            "   numbers you checked (e.g. `run-pr-gates.sh:388-389` or `open-pr.sh:7-10,72-74`).")
    if deslop:
        cite_lines.append(
            "   A `deslop` / `slop_refused` verdict cites the vale RULE NAME and the PHRASE,\n"
            "   plus the fragment-identity `vale N->M` pair. A script:line has nothing to\n"
            "   point at for a wording change.")
    cite_lines.append(
        "   The citation is what makes the verdict auditable, and it is what the\n"
        "   orchestrator writes into the commit body.")
    citation_block = "\n".join(cite_lines)

    produce_extra = ""
    if "progressive-disclosure" in ctx.modules:
        produce_extra = (
            "4. If any verdict is progressive-disclosure, write a proposal to:\n"
            "   %s — one paragraph per rule: what\n"
            "   rule, which verb/note should carry it, and why the tool is the right home.\n"
            % os.path.join(ctx.proposals_dir, "N%04d.md" % n["id"]))
    last_step = 5 if produce_extra else 4
    return f"""# Node {n['id']} — {n['heading'] or '(frontmatter)'}

## Your tasks (answer ALL of them; one subagent per node because they all edit the same lines)
{task_lines}

## The paragraph (node {n['id']}, {n['kind']}, {loc}, {n['word_count']} words)
Heading path: {path_str}

```
{n['content']}
```
{alerts_block}{style_block}
## Your worktree
- Your worktree: {wt} (branch {ctx.worktree_branch(n['id'])}), created from the current
  split-branch HEAD before you started. The skill file in YOUR worktree is
  {ctx.worktree_path(n['id'])}/{ctx.skill_name} — that is the file you edit.
- Locate your section by its heading — {shift_note}

## Context you need
{context_block}

## Policy (modules: {modules_label})
{policy}

## How to investigate
{investigate}

## Verdict options (one per task)
{verdict_options}

## What to produce
1. Edit {ctx.worktree_path(n['id'])}/{ctx.skill_name} — only your section's lines
   (keep = no edit).
2. Produce the diff: `git -C {ctx.worktree_path(n['id'])} diff --no-prefix -- {ctx.skill_name} > {os.path.join(ctx.diffs_dir, 'N%04d.diff' % n['id'])}`
   (an empty file means keep — no change).
3. Write one verdict line per task to: {os.path.join(ctx.verdicts_dir, 'N%04d.md' % n['id'])}
   Format, one line per task:
   `{ex_id} {ex_kind}: keep — one-line reason with its citation`
{citation_block}
{produce_extra}{last_step}. Report back: your verdicts, a one-line reason each, and the diff path.

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

def apply_db_meta(ctx, args):
    """Re-point ctx at the module set and style docs the DB was split with.

    --briefs-only regenerates from the CLI, and the task kinds now depend on the module
    set, so a regeneration under a different module set drops tasks the DB still holds
    pending: a `--modules trim,progressive-disclosure,deslop` DB regenerated with a bare
    --briefs-only lost every deslop-prose task from its brief and renumbered the rest.
    drive-nodes.py already reads the module set back this way. An explicit --modules or
    --style-doc still wins; a missing DB or meta table leaves the CLI values alone.
    """
    if not os.path.exists(ctx.db):
        return
    try:
        conn = sqlite3.connect(ctx.db)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        conn.close()
    except sqlite3.Error:
        return
    if args.modules == ",".join(DEFAULT_MODULES) and meta.get("modules"):
        ctx.modules = normalize_modules(meta["modules"])
    if "deslop" in ctx.modules and not args.style_doc:
        ctx.style_docs = resolve_style_docs(ctx.repo_root, meta.get("style_docs") or None)


def main():
    ap = argparse.ArgumentParser(description="Split a skill markdown file into a DAG of paragraph nodes in sqlite.")
    Ctx.add_args(ap)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-no-style-doc", action="store_true",
                    help="split with --modules deslop even though no writing standard was found; "
                         "every brief then tells its worker to refuse every punctuation, phrasing "
                         "and naming alert")
    ap.add_argument("--briefs-only", action="store_true",
                    help="regenerate briefs from the current skill without touching the DB; the "
                         "module set and style docs come from the DB meta, so the briefs match "
                         "the tasks the DB actually holds (an explicit --modules still wins)")
    ap.add_argument("--next", type=int, default=0, help="print the next N schedulable nodes and exit")
    args = ap.parse_args()
    ctx = Ctx.from_args(args)
    ctx.force = args.force
    ctx.ensure_dirs()
    if args.briefs_only:
        apply_db_meta(ctx, args)

    # A reword with no named writing standard has vale's alert list as its only authority,
    # and 38-49% of the alerts on this corpus are EmDashUsage. That run is a punctuation
    # rewrite, so it does not start by accident.
    if "deslop" in ctx.modules and not ctx.style_docs and not args.allow_no_style_doc:
        sys.exit(
            "--modules deslop needs a writing standard: no --style-doc given, and no "
            "AGENTS.md / CLAUDE.md in %s.\n"
            "Pass --style-doc PATH, or --allow-no-style-doc to split anyway (every brief "
            "then tells its worker to refuse every punctuation, phrasing and naming alert)."
            % (ctx.repo_root or "the doc's directory"))

    fm, lines, body_start = parse_skill(ctx.skill_path)
    fm_node = frontmatter_node(fm, body_start)
    sections = build_sections(lines, body_start)
    nodes = build_nodes(sections, ctx.chunk_words)
    edges = build_edges(nodes)
    tasks = assign_tasks(nodes, fm_node, ctx.tool_name, ctx.modules)

    # snapshot the pristine original for subagent diffing
    with open(ctx.orig_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    if args.briefs_only:
        n_briefs = write_briefs(ctx, nodes, tasks, fm_node)
        print("briefs regenerated: %d (modules: %s) -> %s"
              % (n_briefs, ", ".join(ctx.modules), ctx.briefs_dir))
        return

    write_db(ctx, fm, fm_node, nodes, edges, tasks)
    n_briefs = write_briefs(ctx, nodes, tasks, fm_node)

    # summary
    from collections import Counter
    kinds = Counter(t["kind"] for t in tasks)
    n_edges = len(edges)
    n_chunks = sum(1 for n in nodes if n["kind"] == "chunk")
    print("split %s -> %s" % (ctx.skill_path, ctx.db))
    print("  nodes: %d (%d sections, %d chunks, %d frontmatter)"
          % (len(nodes) + (1 if fm_node else 0), len(nodes) - n_chunks, n_chunks,
             1 if fm_node else 0))
    print("  edges: %d (%s)" % (n_edges, ", ".join("%s=%d" % (k, c) for k, c in Counter(e[2] for e in edges).items())))
    print("  tasks: %d (%s)" % (len(tasks), ", ".join("%s=%d" % (k, c) for k, c in kinds.items())))
    print("  briefs: %d -> %s" % (n_briefs, ctx.briefs_dir))
    print("  original snapshot: %s" % ctx.orig_path)
    print("  tool: %s" % (ctx.tool or "(none)"))
    if "deslop" in ctx.modules:
        print("  style docs: %s" % (", ".join(ctx.style_docs) or "(none — every style alert is refused)"))

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
