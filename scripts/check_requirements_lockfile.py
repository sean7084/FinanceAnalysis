#!/usr/bin/env python
"""Assert the pip-compile lockfiles still match their ``.in`` intent files.

Why this and not ``pip-compile --dry-run`` in CI: re-resolving torch, mlflow and akshare
with hashes took about twenty minutes per file on the host that generated these, and needs
the network. It would be the slowest and flakiest step in the job. The failure that actually
happens is duller -- someone edits ``requirements/*.in`` and forgets to recompile, so the
intent and the locked set disagree. That is a text comparison and takes milliseconds.

What it checks, per pair:
  1. every ``name==version`` in the ``.in`` appears at the same version in the ``.txt``;
  2. every package line in the ``.txt`` carries at least one ``--hash`` -- hash mode is the
     point, and one unhashed line defeats it silently;
  3. the ``.txt`` contains no ``-r`` include -- a compiled file is flattened, so an
     include means it is not the set pip would install.

Usage::

    python scripts/check_requirements_lockfile.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = PROJECT_ROOT / 'requirements'

#: base is compiled first because the other two include it.
PAIRS = ('base', 'local', 'production')

NAME_VERSION = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)', re.IGNORECASE)


def _normalise(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def direct_pins(path):
    """``{normalised name: version}`` for the pinned lines of an ``.in`` file."""
    pins = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        match = NAME_VERSION.match(stripped)
        if match:
            pins[_normalise(match.group(1))] = match.group(2)
    return pins


def locked_pins(path):
    """``{normalised name: (version, hash_line_count)}`` for a compiled ``.txt``."""
    locked = {}
    includes = []
    current = None
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('-r') or stripped.startswith('-c'):
            includes.append(stripped)
            continue
        match = NAME_VERSION.match(stripped)
        if match:
            current = _normalise(match.group(1))
            locked[current] = (match.group(2), 0)
            continue
        if current and stripped.startswith('--hash='):
            version, count = locked[current]
            locked[current] = (version, count + 1)
    return locked, includes


def collect_problems(pairs=PAIRS, root=REQUIREMENTS):
    problems = []
    for stem in pairs:
        intent = root / f'{stem}.in'
        lock = root / f'{stem}.txt'
        if not intent.exists() or not lock.exists():
            missing = f'{stem}.in' if not intent.exists() else f'{stem}.txt'
            problems.append(f'{missing}: file is missing')
            continue

        locked, includes = locked_pins(lock)
        for include in includes:
            problems.append(
                f'{stem}.txt: contains {include!r}; a compiled lockfile must be flattened')

        for name, version in sorted(direct_pins(intent).items()):
            if name not in locked:
                problems.append(f'{stem}.txt: {name}=={version} is not locked')
                continue
            locked_version, hashes = locked[name]
            if locked_version != version:
                problems.append(
                    f'{stem}.txt: {name} locks {locked_version} but the .in asks {version}')
            if not hashes:
                problems.append(f'{stem}.txt: {name} has no --hash line')
    return problems


def main():
    problems = collect_problems()
    if problems:
        on_actions = os.environ.get('GITHUB_ACTIONS')
        for problem in problems:
            print(f'::error::{problem}' if on_actions else problem, file=sys.stderr)
        print(
            '\nRecompile: pip-compile --generate-hashes --no-emit-index-url '
            '--no-emit-trusted-host requirements/<name>.in',
            file=sys.stderr,
        )
        return 1
    print('requirements lockfiles are consistent with their .in files')
    return 0


if __name__ == '__main__':
    sys.exit(main())