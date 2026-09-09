"""prompt_modules.py — composable prompt modules for doc-consolidation subagents.

The prompt is assembled from modules so a run can take all modules (the default)
or a single module (e.g. only progressive-disclosure, to surface embeddable rules on a doc
that was already trimmed in a earlier pass).

Modules:
  trim          — trim text verbatim-restated in the tool to a one-line pointer.
  progressive-disclosure  — disclose rules at the output where the tool REPORTS the
  situation the rule governs (--help for invocation, the failure output for
  failure modes, the status output for status) — not in a header comment.
  deslop        — reword prose a vale ai-tells alert flags as machine-written,
  bounded to the node's own line range and to the alerts the brief lists. The doc
  repo's own writing standard comes first; the alert count is never the goal.

The base (accuracy/consistency/currency checks) always applies; it is not a
selectable module. Its `script:line` citation demand is added only when the doc has
a tool script to cite — policy_text(modules, tool=False) drops it, because on a
tool-free doc there is nothing to point at.

Used by split-skill-dag.py (baked into the brief) and drive-nodes.py (printed in
the spawn task). split records the selected modules in the DB meta table so
drive-nodes reads them back without a flag.
"""

ALL_MODULES = ["trim", "progressive-disclosure", "deslop"]

BASE = (
    "**Always:** verify accuracy and consistency — every claim the doc makes must still\n"
    "be true, and the doc must not contradict itself."
)

BASE_TOOL = (
    "Check every claim against the tool script and cite the specific `script:line` you\n"
    "checked (e.g. `run-pr-gates.sh:388-389`). The citation is what makes the verdict\n"
    "auditable."
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
    "Keep a rule in the doc ONLY if it is genuine cross-script orchestration no single\n"
    "script can self-describe (e.g. 'launch Step 3 in the foreground, Steps 4-5\n"
    "alongside' — that is about the wave, not one script).\n"
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

DESLOP = """**Deslop module:** your brief's "Vale alerts in your line range" section lists
every alert vale raised inside YOUR node's lines. Answer those and ONLY those — never edit
another node's paragraph to clear an alert, even where the same phrase appears there;
another subagent owns those lines and you will collide with it.

**Answer each alert, do not obey it.** An `EmDashUsage` alert points at one em dash.
Sometimes that dash does read as machine-written. Where it carries the doc's own bullet
format, it does not, and you refuse the alert. Judge each alert against the sentence in
front of you, and aim at prose a human would have written.

**These rule families are PRESUMED REFUSED on this corpus**, because they fire on the
house voice, not on machine writing: `EmDashUsage`, `SemicolonUsage`, `NounString`,
`BareHolds`, `Figurative*` (Carries/Owns/Lands/Stays/Wins/Surfaces),
`AnthropomorphicCognition`, `SentenceStartRepetition`. Do not remove an em dash, a
semicolon, or a named compound term to clear an alert. Act on one of these ONLY when the
sentence is separately wrong (it hides the main point, buries two ideas, or goes passive),
and say which. Record the whole presumed-refused set on ONE verdict line:
`slop_refused: house-style (EmDashUsage x6, NounString x1)` — never one line per alert.

**A named term is never reworded to satisfy a vale rule.** `NounString` on a named term
(`the switch key owner id`, `the provider key test verdict`) is refused by definition: the
noun stack IS the name, and "rewrite the stack as a phrase" destroys the grep. Same for
`BareHolds` on `the config store holds ...`, and for the `Figurative*` rules on a verb the
doc has already established.

**The doc repo's own writing standard comes first.** Your brief's "Writing standard"
section names the style doc for this run. Open it and hold its rules while you edit: main
point first, one idea per sentence, short words, active voice, cut filler, name things in
the doc's own vocabulary. If your brief names NO style doc, refuse EVERY punctuation,
phrasing, and naming alert and report them unacted — do not guess the house voice.

**NEVER rewrite quoted material:** error strings, log lines, command output, provider
responses, and anything inside a code fence. Paraphrasing destroys its evidential value.
Vale skips code fences and masks inline `code` with asterisks, so it cannot tell a quote
from prose; you must. Code identifiers keep their real names — `providerKeyTestVerdict`
never becomes "the verdict struct".

**Preserve meaning EXACTLY.** This module changes wording, never facts. If the only way to
clear an alert is to drop a fact, a number, or a filename, keep the fact and refuse the
alert.

**MEASURE the result on the FRAGMENT, never on a line range.** Line numbers drift as soon
as anyone edits above you, so a re-run keyed to your brief's range measures the wrong
prose: in one measured simulation it hid 2 of 4 new rules, in another it read a real
regression as flat. Compare the node's text before against the node's text after, as two
standalone files:

```bash
B=$(mktemp -d)
cat > "$B/before.md" <<'VALE_EOF'
<paste the node text EXACTLY as the brief's fenced block shows it>
VALE_EOF
cat > "$B/after.md" <<'VALE_EOF'
<paste the replacement text EXACTLY as it now stands in your worktree>
VALE_EOF
for f in before after; do
  vale --output=JSON "$B/$f.md" | jq -r '.[][] | [.Check, .Match] | @tsv' | sort > "$B/$f.tsv"
  test -s "$B/$f.tsv" || echo "CONTROL FAILED for $f.md: 0 rows. Prove vale linted it before reporting a zero."
done
echo "vale $(wc -l < "$B/before.tsv") -> $(wc -l < "$B/after.tsv")"
echo "new rules: $(comm -13 <(cut -f1 "$B/before.tsv" | sort -u) <(cut -f1 "$B/after.tsv" | sort -u) | paste -sd, -)"
```

Four rules that recipe rests on:
  1. Both files MUST end in `.md`, and NEVER pass `--glob` with an explicit file path.
     `--glob` is a directory-walk filter: on a path it does not match, vale prints `{}`
     and exits 0. Measured on identical bytes — `--glob='*.md'` plus a `.txt` name = 0
     alerts, no glob plus `.txt` = 110, no glob plus `.md` = 68.
  2. A zero is a CONTROL FAILURE until you prove otherwise. Report the control failure,
     never the zero.
  3. Compare fragment to fragment only. Never compare an after-fragment against a
     whole-file count: window rules fire differently out of context (measured, one node
     scored 21 as a fragment and 20 inside the whole-file run).
  4. Report the RULE-NAME diff, not the counts alone. A count stays flat while one slop
     pattern is traded for another.

(vale exits non-zero when it finds alerts, which is not an error: capture its output
before you test the status, and never chain `&& jq` after it. Its config is global, so it
runs from any directory with no per-repo `.vale.ini`.)

**Report two lists, never a total.** One list of the alerts you ACTED ON (rule + phrase +
what was wrong with the sentence beyond the alert), one of the alerts you REFUSED (rule +
count). A lower total is NOT the goal, and a run that clears every alert is a failure
signal rather than a success. Name any rule that shows up in the after run and not in the
before run, and put the literal `vale N->M` pair in the verdict reason so the number is
checkable.

Cite the vale rule name and the offending phrase for every verdict this module produces. A
`script:line` citation has nothing to point at for a wording change.

Verdicts this module adds: **deslop** (rewrote the prose, meaning unchanged, rule + phrase
named), **slop_refused** (the alert is wrong for this doc — name the rule and why the
house standard beats it). Write `slop_refused` with an underscore: the verdict parser stops
at a hyphen."""


MODULES = {"trim": TRIM, "progressive-disclosure": PROGRESSIVE_DISCLOSURE, "deslop": DESLOP}


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


def policy_text(modules, tool=True):
    """Assemble the policy block: base + each selected module.

    tool=False drops the `script:line` citation demand. A --no-tool run (or a doc whose
    tool is a directory of scripts) has no script line to cite, and the demand then
    contradicts every verdict the worker can honestly write.
    """
    parts = [BASE]
    if tool:
        parts.append(BASE_TOOL)
    parts += [MODULES[m] for m in modules if m in MODULES]
    return "\n\n".join(parts)


def verdict_options_text(modules):
    """Assemble the verdict-options list, led by 'keep' (always available)."""
    lines = ["- **keep** — the paragraph is accurate, non-redundant, and needed before the tool runs (or is genuine cross-script orchestration no single script self-describes)."]
    if "trim" in modules:
        lines.append("- **trim-to-pointer** — the trimmed text is VERBATIM restated in the tool's header / a code comment / `--help` / a note; keep one line that names the finding and points at the script. If it is only partially discoverable, keep it.")
        lines.append("- **delete** — fully redundant or superseded; nothing of value remains.")
    if "progressive-disclosure" in modules:
        lines.append("- **progressive-disclosure** — the rule governs a situation the tool PRODUCES or REPORTS (an invocation, a failure, a status), the tool does NOT already disclose it at that output, and it belongs there so one run gives the situation AND the rule. Write a proposal naming the output location + the text to ADD; do NOT edit the tool script. Trim the doc's copy to a pointer that names the command + situation — never 'see the header'.")
    if "deslop" in modules:
        lines.append("- **deslop** — a vale alert inside your line range flagged prose that reads as machine-written, the sentence was wrong beyond the alert itself, and you rewrote it to the house standard with every fact intact. Name the vale rule, the phrase you changed, and the fragment-identity `vale N->M` pair.")
        lines.append("- **slop_refused** — the alert is wrong for this doc: it fires on the house voice (one of the presumed-refused families), or obeying it would reword a named term, paraphrase quoted material or a code identifier, or drop a fact. Keep the text as written; name the rule you refused and why the house standard beats it. Underscore, not hyphen — the verdict parser stops at a hyphen.")
    return "\n".join(lines)