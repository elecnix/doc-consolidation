"""prompt_modules.py — composable prompt modules for doc-consolidation subagents.

The prompt is assembled from modules so a run can take all modules (the default)
or a single module (e.g. only progressive-disclosure, to surface embeddable rules on a doc
that was already trimmed in a earlier pass).

Modules:
  trim          — trim text verbatim-restated in the tool to a one-line pointer.
  progressive-disclosure  — propose embedding rules (about a script's behavior) into the script.

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
    "**Move-to-tool module:** for each decision rule, failure-mode, `never`/`always`\n"
    "rule, or behavioral constraint that is ABOUT a tool script's behavior or\n"
    "invocation, check whether the script ALREADY PRINTS it when you run\n"
    "`<script> --help` (or the script itself, if it prints guidance on a bare run).\n"
    "If it does NOT, write a **progressive-disclosure** proposal — but the rule must go into the\n"
    "script's RUNTIME OUTPUT, not a header comment. The whole point is: a single\n"
    "invocation (`<script> --help`) gives the caller the status AND the rule, in one\n"
    "LLM turn. A rule buried in a header comment the agent must `read` the file to\n"
    "find is friction and a fragile pointer — do not propose that. The proposal names:\n"
    "(a) the rule verbatim from the doc, (b) the target script, (c) the exact\n"
    "--help/usage/stdout text to ADD so the rule PRINTS on invocation (quote the\n"
    "block that will appear in `--help` output — if the script has no --help, the\n"
    "proposal adds one, or prints the rule on a bare run), (d) why the tool is the\n"
    "right home (a caller running it learns the rule in the same turn). Do NOT edit\n"
    "the tool script — write the proposal only. Bias toward proposing: when in doubt\n"
    "whether a rule is embeddable in --help output, propose it; the orchestrator\n"
    "decides whether to implement. Trim a moved rule in the doc to a one-line pointer\n"
    "that says `run <script> --help` for the rule — never 'see the header'.\n"
    "Verdict this module adds: **progressive-disclosure** (rule about a script's behavior, not\n"
    "yet PRINTED by the script → write a proposal that puts it in --help output)."
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
        lines.append("- **progressive-disclosure** — the rule is ABOUT a tool script's behavior or invocation, the script does NOT already PRINT it on `<script> --help` (or a bare run), and it belongs in the script's RUNTIME OUTPUT so one invocation teaches it. Write a proposal file that quotes the --help/usage text to ADD; do NOT edit the tool script. Trim the doc's copy to `run <script> --help` for the rule — never 'see the header'.")
    return "\n".join(lines)