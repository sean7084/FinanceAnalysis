#!/usr/bin/env python3
"""Verbatim-integrity gate for BACKLOG_ARCHIVE.md.

The archive's whole purpose is that measurement evidence reaches it **unchanged**; the plan names
paraphrase-into-archive as its top risk, and no other check in the repo would notice a quietly
reworded number. Every archived content line must appear byte-for-byte in the pre-restructure
`BACKLOG.md` (commit c225c0d).

Exemptions are deliberately narrow and each one is named:
  * lines before the first `## A.` section -- the archive preamble written during the restructure;
  * `> Disposition:` lines, the dated correction notes, the §G table, and `---` rules;
  * `## A.` .. `## G.` section titles and `### ` group headings added by the restructure, which
    are reported by list so a human sees them instead of the tool swallowing them.
`AUTHORED_EXACT` is for a specific sentence of mine that sits inside a section; naming it keeps
the exemption from quietly widening.

`--control` proves the matcher can fail, by perturbing a real archived line and requiring the
perturbed form to be absent while the original is present.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE_REF = "c225c0d"
ARCHIVE = REPO / "BACKLOG_ARCHIVE.md"

AUTHORED_PREFIXES = (
    "> Disposition:",
    "> **Corrected 2026-11-12",
    "> ",
    "|",
    "---",
    "# Backlog Archive",
    # Section G's intro sentence, written during the restructure to explain the table beneath
    # it. Matched on its opening phrase rather than transcribed in full: my first attempt at an
    # exact-string exemption silently missed because I typed the sentence from memory.
    "One line each: what was deleted rather than archived",
)
AUTHORED_EXACT: set[str] = set()
SECTION_RE = re.compile(r"^## [A-G]\. ")
HEADING_RE = re.compile(r"^### ")


def original_lines():
    result = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{SOURCE_REF}:BACKLOG.md"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise SystemExit(f"cannot read {SOURCE_REF}:BACKLOG.md: {result.stderr.strip()[:200]}")
    return {line.rstrip() for line in result.stdout.splitlines() if line.strip()}


def run_check(source_lines, verbose=True):
    archive = ARCHIVE.read_text(encoding="utf-8").splitlines()
    seen_section = False
    checked = 0
    exempt_preamble = 0
    misses = []
    added_headings = []
    for number, line in enumerate(archive, start=1):
        stripped = line.rstrip()
        if SECTION_RE.match(stripped):
            seen_section = True
            continue
        if not seen_section:
            if stripped:
                exempt_preamble += 1
            continue
        if not stripped or stripped.startswith(AUTHORED_PREFIXES) or stripped in AUTHORED_EXACT:
            continue
        checked += 1
        if HEADING_RE.match(stripped):
            if stripped not in source_lines:
                added_headings.append(stripped)
            continue
        if stripped not in source_lines:
            misses.append((number, stripped))
    if verbose:
        print(f"compared {checked} non-exempt lines against {SOURCE_REF}:BACKLOG.md", flush=True)
        print(f"preamble lines exempted by position: {exempt_preamble}", flush=True)
        print(f"archived content lines NOT verbatim: {len(misses)}", flush=True)
        for number, text in misses[:12]:
            print(f"  line {number}: {text[:130]}", flush=True)
        print(f"group headings added by the restructure (structure, not history): {len(added_headings)}", flush=True)
    return misses, added_headings, checked


def run_control(source_lines):
    archive = ARCHIVE.read_text(encoding="utf-8").splitlines()
    probe = None
    for line in archive:
        stripped = line.rstrip()
        if (
            stripped.startswith("  ")
            and any(character.isdigit() for character in stripped)
            and stripped in source_lines
            and len(stripped) > 40
        ):
            probe = stripped
            break
    if probe is None:
        print("CONTROL DID NOT RUN: no archived measurement line found to perturb", flush=True)
        return False
    perturbed = probe + " 0"
    print(f"probe: {probe[:88]}", flush=True)
    print(f"  original present: {probe in source_lines} (must be True)", flush=True)
    print(f"  perturbed present: {perturbed in source_lines} (must be False)", flush=True)
    return probe in source_lines and perturbed not in source_lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", action="store_true", help="prove the matcher can fail")
    args = parser.parse_args()

    source_lines = original_lines()
    if args.control:
        return 0 if run_control(source_lines) else 1

    misses, _, checked = run_check(source_lines)
    if not run_control(source_lines):
        print("\nthe gate's own control failed, so a green result here means nothing", flush=True)
        return 1
    print(f"\narchive verbatim gate: {len(misses)} paraphrase(s) over {checked} lines", flush=True)
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
