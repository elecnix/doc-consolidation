"""prompt_modules.py — composable prompt modules for doc-consolidation subagents.

The prompt is assembled from modules so a run can take all modules (the default)
or a single module (e.g. only move-to-tool, to surface embeddable rules on a doc
that was already trimmed in a earlier pass).

Modules:
  trim          — trim text verbatim-restated in the tool to a one-line pointer.
  move-to-tool  — propose embedding rules (about a script's behavior) into the script.

The base (accuracy/consistency/currency checks, script:line citations, keep-if-
cross-script-orchestration) always applies; it is not a selectable module.

Used by split-skill-dag.py (baked into the brief) and drive-nodes.py (printed in
the spawn task). split records the selected modules in the DB meta table so
drive-nodes reads them back without a flag.
"""

ALL_MODULES = ["trim", "move-to-tool"]

BASE = (
    "**Always:** verify accuracy and consistency against the tool script — cite the\n"
    "specific `script:line` for every claim you checked (e.g. `run-pr-gates.sh:388-389`).\n"
    "Keep a rule in the doc ONLY if it is genuine cross-script orchestration no single\n"
    "script can self-describe (e.g. 'launch Step 3 in the foreground, Steps 4-5\n"
    "alongside' — that is about the wave, not one script)."
)

TRIM = (
    "**Trim module:** only trim text that is VERBATIM restated in the tool's header, a\n"
    "code comment, or `--help`/note output. KEEP every decision rule, failure-mode\n"
    "illustration, and `never`/`always` rule even if it is partially discoverable —\n"
    "partial discoverability is NOT enough to delete. When in doubt, keep.\n"
    "Verdicts this module adds: **trim-to-pointer** (verbatim-restated → one line that\n"
    "names the finding and points at the script), **delete** (fully redundant or\n"
    "superseded)."
)

MOVE_TO_TOOL = (
    "**Move-to-tool module:** for each decision rule, failure-mode, `never`/`always`\n"
    "rule, or behavioral constraint that is ABOUT a tool script's behavior or\n"
    "invocation, check whether the script ALREADY states it in its\n"
    "header/--help/code-comments. If it does NOT, write a **move-to-tool** proposal:\n"
    "(a) the rule verbatim from the doc, (b) the target script and exact location to\n"
    "embed it (header / --help / code-comment at line N), (c) the proposed wording to\n"
    "add to the script, (d) why the tool is the right home (a reader running the script\n"
    "would find it there). Do NOT edit the tool script — write the proposal only. Bias\n"
    "toward proposing: when in doubt whether a rule is embeddable, propose it; the\n"
    "orchestrator decides whether to implement. Trim a moved rule in the doc to a\n"
    "one-line pointer saying the script's header documents why.\n"
    "Verdict this module adds: **move-to-tool** (rule about a script's behavior, not yet\n"
    "stated in the script → write a proposal)."
)

MODULES = {"trim": TRIM, "move-to-tool": MOVE_TO_TOOL}


def normalize(modules_arg):
    """Parse a comma-separated module list; default to all; preserve order, drop dups."""
    if not modules_arg:
        return list(ALL_MODULES)
    parts = [m.strip() for m in modules_arg.split(",") if m.strip()]
    out = []
    for p in parts:
        if p not in MODULES:
            raise ValueError("unknown module %r (choose from %s)" % (p, ", ".join(ALL_MODULES)))
        if p not in out:
            out.append(p)
    return out


def policy_text(modules):
    """Assemble the policy block: base + each selected module."""
    parts = [BASE] + [MODULES[m] for m in modules if m in MODULES]
    return "\n\n".join(parts)


def verdict_options_text(modules):
    """Assemble the verdict-options list, led by 'keep' (always available)."""
    lines = ["- **keep** — the paragraph is accurate, non-redundant, and needed before the tool runs (or is genuine cross-script orchestration no single script self-describes)."]
    if "trim" in modules:
        lines.append("- **trim-to-pointer** — the trimmed text is VERBATIM restated in the tool's header / a code comment / `--help` / a note; keep one line that names the finding and points at the script. If it is only partially discoverable, keep it.")
        lines.append("- **delete** — fully redundant or superseded; nothing of value remains.")
    if "move-to-tool" in modules:
        lines.append("- **move-to-tool** — the rule is ABOUT a tool script's behavior or invocation, the script does NOT already state it, and it belongs embedded in the script's header/--help/code-comments. Write a proposal file (see below); do NOT edit the tool script. Trim the doc's copy to a one-line pointer that says the script's header documents why.")
    return "\n".join(lines)