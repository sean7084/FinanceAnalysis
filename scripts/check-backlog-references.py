#!/usr/bin/env python3
"""Backlog reference gate: markdown links must resolve, prose mentions must not be mistaken for them.

Added after the restructure audit produced three "broken" hits that were not broken:
`apps/sentiment/tests_scoring.py` (an app deleted later, named in archived prose),
`apps/users/views.py` (a bug description from before a rename) and HomeServer's
`docs/runbooks/minio.md` (another repository's path). Rewriting verbatim archive text to
satisfy a link checker would be the wrong trade, and a gate that reports those as failures
gets ignored, which is worse than no gate. So: only Markdown links and index pointers are
required to resolve, and the check can still fail -- `--self-test` proves it.
"""

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "BACKLOG.md"
ARCHIVE = REPO / "BACKLOG_ARCHIVE.md"
EXTERNAL_REPOS = ("HomeServer",)

LINK_RE = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:|#[^)])([^)#]+)")
CODE_REF_RE = re.compile(r"`(apps/[A-Za-z0-9_/.]+|docs/[A-Za-z0-9_/.]+|scripts/[A-Za-z0-9_/.]+)`")


def markdown_link_failures(text, source):
    failures = []
    for target in LINK_RE.findall(text):
        candidate = (REPO / target.strip()).resolve()
        if not candidate.exists():
            failures.append(f"{source}: markdown link target missing: {target}")
    return failures


def index_pointer_failures(text, source):
    """In the thin index, a backticked path is a live pointer and must exist.

    In the archive it is quoted history and is deliberately not checked, because the file
    it names may have been deleted by the very change the entry describes.
    """
    failures = []
    for reference in CODE_REF_RE.findall(text):
        if any(external in reference for external in EXTERNAL_REPOS):
            continue
        if "*" in reference:
            continue
        if not (REPO / reference).exists():
            failures.append(f"{source}: index pointer missing: {reference}")
    return failures


def referring_files_failure():
    """Every document that points at BACKLOG.md must point at a file that exists."""
    failures = []
    for path in REPO.rglob("*.md"):
        if path.name.startswith("BACKLOG"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for target in LINK_RE.findall(text):
            if "BACKLOG" in target and not (path.parent / target).resolve().exists() \
               and not (REPO / target).exists():
                failures.append(f"{path.relative_to(REPO)}: dangling pointer to the backlog: {target}")
    return failures


def self_test():
    """The gate must be able to fail. Each fixture is a defect that has to be caught."""
    fixtures = [
        ("dangling markdown link", "see [the issue](docs/does-not-exist.md)"),
        ("dangling index pointer", "handled in `apps/core/no_such_module.py`"),
    ]
    caught = 0
    for label, text in fixtures:
        found = markdown_link_failures(text, "fixture") + index_pointer_failures(text, "fixture")
        if found:
            caught += 1
            print(f"  caught: {label} -> {found[0].split(': ', 1)[1]}")
        else:
            print(f"  MISSED: {label}")
    # And the case that must NOT fail: the real archive, which quotes paths that were
    # deleted after the entries were written. Asserted against the file itself, so the
    # fixture cannot drift into checking something the gate never does.
    archive_text = ARCHIVE.read_text(encoding="utf-8")
    prose_mentions = CODE_REF_RE.findall(archive_text)
    missing_in_reality = [reference for reference in prose_mentions if not (REPO / reference).exists()]
    if not missing_in_reality:
        print("  VACUOUS: the archive no longer names any deleted path, so this proves nothing")
        return False
    link_only_failures = markdown_link_failures(archive_text, ARCHIVE.name)
    print(
        f"  archive prose: {len(missing_in_reality)} deleted-path mention(s) present "
        f"(e.g. {missing_in_reality[0]}), flagged as link failures: {len(link_only_failures)}"
    )
    return caught == len(fixtures) and not link_only_failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true", help="prove the gate can fail")
    args = parser.parse_args()

    if args.self_test:
        print("running gate self-test", flush=True)
        return 0 if self_test() else 1

    failures = []
    for path in (INDEX, ARCHIVE):
        text = path.read_text(encoding="utf-8")
        failures += markdown_link_failures(text, path.name)
        if path == INDEX:
            failures += index_pointer_failures(text, path.name)
    failures += referring_files_failure()

    print(f"backlog reference gate: {len(failures)} failure(s)", flush=True)
    for failure in failures:
        print(f"  {failure}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
