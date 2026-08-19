"""prompt_modules.py — composable prompt modules for doc-consolidation subagents.

The prompt is assembled from modules so a run can take all modules (the default)
or a single module (e.g. only progressive-disclosure, to surface embeddable rules on a doc
that was already trimmed in a earlier pass).

Modules:
  trim          — trim text verbatim-restated in the tool to a one-line pointer.
  progressive-disclosure  — disclose rules at the output where the tool REPORTS the
  situation the rule governs (--help for invocation, the failure output for
  failure modes, the status output for status) — not in a header comment.

The base (accuracy/consistency/currency checks, script:line citations, keep-if-
cross-script-orchestration) always applies; it is not a selectable module.

Used by split-skill-dag.py (baked into the brief) and drive-nodes.py (printed in
the spawn task). split records the selected modules in the DB meta table so
drive-nodes reads them back without a flag.
"""

ALL_MODULES = ["trim", "progressive-disclosure"]

BASE = (
    "**Always:** verify accuracy and consistency against the tool script — cite the\n"
    "specific `script:line` for every claim you checked (e.g. `run-pr-gates.sh:388-389`).\n"
    "Keep a rule in the doc ONLY if it is genuine cross-script orchestration no single\n"
    "script can self-describe (e.g. 'launch Step 3 in the foreground, Steps 4-5\n"
    "alongside' — that is about the wave, not one script)."
)

TRIM = (
    "**Trim module:** only trim text that is VERBATIM restated in the tool's\n"
    "`--help`/usage OUTPUT or what the script PRINTS when run — not text buried in a\n"
    "header comment you have to open the file to read. (A pointer that says 'see the\n"
    "header' is friction: the agent must then run a read tool to find the lines, and\n"
    "the pointer is fragile. A pointer that says 'run `<script> --help`' teaches the\n"
    "rule in one command.) KEEP every decision rule, failure-mode illustration, and\n"
    "`never`/`always` rule even if it is partially discoverable — partial\n"
    "discoverability is NOT enough to delete. When in doubt, keep.\n"
    "Verdicts this module adds: **trim-to-pointer** (verbatim-restated → one line that\n"
    "says `run <script> --help` for it), **delete** (fully redundant or superseded)."
)

PROGRESSIVE_DISCLOSURE = (
    "**Progressive-disclosure module:** for each decision rule, failure-mode,\n"
    "`never`/`always` rule, or behavioral constraint that is ABOUT a tool script's\n"
    "behavior, find the output where the tool PRODUCES or REPORTS the situation the\n"
    "rule governs — and put the rule THERE. The best place is not always `--help`;\n"
    "it is the output the caller is reading WHEN the rule bites:\n"
    "  - An INVOCATION rule (how to call the script, what to pass) → `--help` / usage.\n"
    "  - A FAILURE-MODE rule (what a red result means, what to do next) → the\n"
    "    script's FAILURE output — print the guidance alongside the failure, where\n"
    "    the caller sees it the moment the situation occurs.\n"
    "  - A STATUS rule (what a printed state implies) → the status output itself.\n"
    "The principle: information that is only useful when observing a specific\n"
    "situation is disclosed at the moment the tool reports that situation — so one\n"
    "run gives the caller the situation AND the rule that governs it, in a single\n"
    "LLM turn. A rule buried in a header comment the agent must `read` the file to\n"
    "find is friction and a fragile pointer — do not propose that.\n"
    "How to check: run `<script> --help` AND exercise the situation the rule is\n"
    "about (trigger the failure, observe the status). If the rule is not already\n"
    "printed at that point, write a **progressive-disclosure** proposal naming:\n"
    "(a) the rule verbatim, (b) the target script, (c) the exact output location and\n"
    "the text to ADD there (quote the block that will print — in `--help`, in the\n"
    "failure path, or in the status output), (d) why that output is the right home\n"
    "(the caller is already reading it when the rule applies). Do NOT edit the tool\n"
    "script — write the proposal only. Bias toward proposing: when in doubt whether\n"
    "a rule is embeddable in the tool's output, propose it; the orchestrator decides\n"
    "whether to implement. Trim a moved rule in the doc to a one-line pointer that\n"
    "names the command + situation (e.g. `run <script> --help`, or `the failure\n"
    "output explains`) — never 'see the header'.\n"
    "Verdict this module adds: **progressive-disclosure** (rule about a situation the\n"
    "tool reports, not yet disclosed at that output → write a proposal that puts it\n"
    "there)."
)

MODULES = {"trim": TRIM, "progressive-disclosure": PROGRESSIVE_DISCLOSURE}


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
    if "progressive-disclosure" in modules:
        lines.append("- **progressive-disclosure** — the rule governs a situation the tool PRODUCES or REPORTS (an invocation, a failure, a status), the tool does NOT already disclose it at that output, and it belongs there so one run gives the situation AND the rule. Write a proposal naming the output location + the text to ADD; do NOT edit the tool script. Trim the doc's copy to a pointer that names the command + situation — never 'see the header'.")
    return "\n".join(lines)