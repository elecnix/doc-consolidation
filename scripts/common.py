#!/usr/bin/env python3
"""common.py — shared path/default derivation for the doc-consolidation scripts.

Every script used to hardcode a single skill's layout — a fixed split worktree,
a fixed tool script, a fixed cache dir, and a fixed worktree root.


To run against any other doc, point --skill at its file and everything else derives:

  base_dir    = <cache-root>/<slug-of-skill-name>           (all artifacts live here)
  diffs/verdicts/proposals/briefs = subdirs of base_dir
  repo_root   = nearest git root above --skill
  skill_rel   = --skill relative to repo_root (the file subagents edit + commit)
  wt_dir      = <wt-root>/<slug>                            (per-node worktree checkout dir)
  branch      = "split-node-<id>" from the skill repo's current branch

The tool is resolved separately because it may live in a different repo (a sibling
skill under ~/.agents/skills): any executable in the doc repo's `scripts/` directory
is auto-detected, and --tool / --no-tool override the heuristics (use --no-tool when
the doc's tool is a directory of scripts, e.g. `tools/ci/`, that --tool can't name as
one file). The --co-author commit trailer defaults to git config user.name/email,
so committing never leaks a hardcoded identity.

Usage:
  from common import Ctx
  args = parser.parse_args(...)     # or any argparse.Namespace with the fields
  ctx  = Ctx.from_args(args)
"""

import argparse
import glob
import os
import re
import subprocess

CACHE_ROOT = os.path.expanduser("~/.cache")


def git(repo, *args):
    """Run git in repo, return CompletedProcess. Never raises."""
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def git_root(path):
    """Nearest enclosing git repo root for a file path, or None."""
    d = os.path.abspath(path)
    while True:
        r = git(d, "rev-parse", "--show-toplevel")
        if r.returncode == 0:
            return r.stdout.strip()
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd


def slugify(s, limit=48):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:limit] or "doc"


GENERIC_DOC_NAMES = {"skill", "skills", "sk", "readme", "readme.md", "doc", "docs"}


def doc_slug(skill_path):
    """Unique, stable slug for a doc: cache dirs and worktree dirs are keyed by it.

    A distinctive basename (release-checklist.md) is used as-is; a generic one
    (SKILL.md, README.md) gets the repo dir prefixed so two skills never collide:
    doc-consolidation-skill vs orchestrate-pi-agents-skill.
    """
    base = os.path.basename(skill_path).rsplit(".", 1)[0].lower()
    repo = git_root(skill_path)
    if repo and base in GENERIC_DOC_NAMES:
        base = "%s-%s" % (slugify(os.path.basename(repo)), base)
    return slugify(base)


def default_db_for(skill_path):
    """~/.cache/<doc_slug>/skill-dag.db — one DB per doc, never shared across docs."""
    return os.path.join(CACHE_ROOT, doc_slug(skill_path), "skill-dag.db")


def tool_candidates(skill_path, repo_root):
    """Ordered candidates for the tool script; first existing one wins.

    A doc's "tool" is the checked implementation whose help/header the doc must not
    restate. It is auto-detected: any executable script in the doc repo's `scripts/`
    directory (the first one, alphabetically), then any in the skill's own
    `~/.agents/skills/<doc>/scripts/`. Pass --tool PATH to override, or --no-tool to
    disable (e.g. when the doc's tool is a directory of scripts like `tools/ci/`).
    """
    candidates = []
    if repo_root:
        scripts_dir = os.path.join(repo_root, "scripts")
        if os.path.isdir(scripts_dir):
            for name in sorted(os.listdir(scripts_dir)):
                p = os.path.join(scripts_dir, name)
                if os.access(p, os.X_OK) and not name.endswith((".pyc", "__pycache__")):
                    candidates.append(p)
    slug = slugify(os.path.basename(skill_path).rsplit(".", 1)[0])
    skill_scripts = os.path.expanduser("~/.agents/skills/%s/scripts" % slug)
    if os.path.isdir(skill_scripts):
        for name in sorted(os.listdir(skill_scripts)):
            p = os.path.join(skill_scripts, name)
            if os.access(p, os.X_OK) and not name.endswith((".pyc", "__pycache__")):
                candidates.append(p)
    return candidates


def resolve_tool(skill_path, repo_root, explicit, no_tool):
    """Resolve the tool script path: explicit flag, then first existing candidate, else None."""
    if no_tool:
        return None
    if explicit:
        return os.path.abspath(explicit)
    for c in tool_candidates(skill_path, repo_root):
        if os.path.exists(c):
            return c
    return None


def coauthor_from_git(skill_path):
    """(name, email) from git config of the doc's repo (falls back to global).

    Returns (None, None) when no user.name is configured.
    """
    repo_root = git_root(skill_path)
    repo = repo_root if repo_root else os.path.expanduser("~")
    r = git(repo, "config", "user.name")
    name = r.stdout.strip() if r.returncode == 0 else ""
    e = git(repo, "config", "user.email")
    email = e.stdout.strip() if e.returncode == 0 else ""
    return (name or None, email or None)


class Ctx:
    """All derived paths + tool + co-author, one place. Read-only."""

    def __init__(self, skill_path, db, repo_root, tool, coauthor, coauthor_email, wt_root,
                 branch_prefix, split_branch, chunk_words, modules=None):
        from prompt_modules import normalize as _normalize_modules
        self.modules = _normalize_modules(modules) if modules else list(__import__("prompt_modules").ALL_MODULES)
        self.skill_path = os.path.realpath(skill_path)   # resolve symlinks: /tmp -> /private/tmp
        self.skill_name = os.path.basename(self.skill_path)
        self.slug = doc_slug(self.skill_path)
        self.db = os.path.abspath(db)
        self.base = os.path.dirname(self.db)
        self.orig_path = os.path.join(self.base, "SKILL.orig.md")
        self.diffs_dir = os.path.join(self.base, "diffs")
        self.proposals_dir = os.path.join(self.base, "proposals")
        self.verdicts_dir = os.path.join(self.base, "verdicts")
        self.briefs_dir = os.path.join(self.base, "briefs")
        self.repo_root = repo_root            # None if --skill is not in a git repo
        if repo_root and not self.skill_path.startswith(repo_root + os.sep):
            # skill path escaped the repo root (e.g. symlinked dirs): fall back to basename
            self.skill_rel = self.skill_name
        else:
            self.skill_rel = os.path.relpath(self.skill_path, repo_root) if repo_root else self.skill_name
        self.tool = tool                      # absolute path or None
        self.tool_name = os.path.basename(tool) if tool else None
        self.coauthor = coauthor
        self.coauthor_email = coauthor_email
        self.wt_root = os.path.abspath(wt_root)
        self.wt_dir = os.path.join(self.wt_root, self.slug)
        self.branch_prefix = branch_prefix
        self.split_branch = split_branch     # branch the doc repo is on (may be "")
        self.chunk_words = chunk_words

    # ------------------------------------------------------------------ derivation helpers

    def ensure_dirs(self):
        for d in (self.base, self.diffs_dir, self.proposals_dir,
                  self.verdicts_dir, self.briefs_dir):
            os.makedirs(d, exist_ok=True)
        return self

    def brief_path(self, node_id):
        matches = glob.glob(os.path.join(self.briefs_dir, "N%04d-*.md" % node_id))
        return matches[0] if matches else None

    def worktree_path(self, node_id):
        return os.path.join(self.wt_dir, "N%04d" % node_id)

    def worktree_branch(self, node_id):
        return "%s%d" % (self.branch_prefix, node_id)

    def coauthor_trailer(self):
        """'Co-authored-by: <name> <<email>>' or '' when no coauthor."""
        if not self.coauthor:
            return ""
        email = self.coauthor_email or (
            re.sub(r"[^a-z0-9]+", "-", self.coauthor.lower()).strip("-") + "@pi-agent.local")
        return "Co-authored-by: %s <%s>" % (self.coauthor, email)

    # ------------------------------------------------------------------ construction

    @classmethod
    def add_args(cls, parser):
        """Attach the shared CLI flags to any argparse parser.

        --skill defaults to None so callers can tell 'not passed' from 'passed the
        default' (apply-node.py reads the source doc from DB meta when omitted).
        """
        parser.add_argument("--skill", default=None,
                            help="markdown doc to consolidate (default: ~/Source/skill-split/SKILL.md)")
        parser.add_argument("--db", default=None,
                            help="sqlite DB path (default: ~/.cache/<skill-name>/skill-dag.db)")
        parser.add_argument("--chunk-words", type=int, default=500,
                            help="max words per node before a section is chunked (default: 500)")
        parser.add_argument("--tool", default=None,
                            help="tool script to verify against (default: auto-detect; --no-tool disables)")
        parser.add_argument("--no-tool", action="store_true",
                            help="disable tool verification (skips tool-dependent checks)")
        parser.add_argument("--co-author", default=None,
                            help="commit trailer name (default: git config user.name of the doc repo)")
        parser.add_argument("--wt-root", default=os.path.expanduser("~/Source/doc-split-wt"),
                            help="root for per-node subagent worktrees (default: ~/Source/doc-split-wt)")
        parser.add_argument("--modules", default="trim,progressive-disclosure",
                            help="comma-separated prompt modules to apply (default: trim,progressive-disclosure = all; "
                                 "choices: trim, progressive-disclosure). A progressive-disclosure-only run surfaces embeddable "
                                 "rules on a doc already trimmed in an earlier pass.")
        return parser

    @classmethod
    def from_args(cls, args):
        """Build a Ctx from a parsed argparse Namespace (see add_args)."""
        skill_path = os.path.realpath(args.skill) if args.skill else \
            os.path.expanduser("~/Source/skill-split/SKILL.md")
        repo_root = git_root(skill_path)
        tool = resolve_tool(skill_path, repo_root, args.tool, args.no_tool)
        db = args.db if args.db else default_db_for(skill_path)
        coauthor = args.co_author
        coauthor_email = None
        if coauthor:
            pass  # email unknown — trailer falls back to <name>@pi-agent.local
        else:
            coauthor, coauthor_email = coauthor_from_git(skill_path)
        split_branch = ""
        if repo_root:
            r = git(repo_root, "branch", "--show-current")
            if r.returncode == 0:
                split_branch = r.stdout.strip()
        return cls(skill_path, db, repo_root, tool, coauthor, coauthor_email, args.wt_root,
                   "split-node-", split_branch, args.chunk_words, args.modules)
