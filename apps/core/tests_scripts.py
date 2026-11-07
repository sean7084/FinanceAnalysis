"""Tests for the ``scripts/`` launchers that start the local stack.

Nothing in ``apps/`` had a harness for these before this file, which is how
``scripts/_check_migrations.sh`` shipped an anchored grep that could never match
and stayed silently inert for its whole life, and how ``_native_env.sh`` assigned
the two variables its child callers require without exporting them -- which made
every backend start through ``run_backend.sh`` die before ``exec runserver``.
Both failures were invisible because the pipeline wrapper reported the exit status
of its last element rather than the service's.

The precedent here is ``apps/core/tests_frontend.py``'s ``PageLoadLatencyHarnessTests``,
which reads script text and asserts invariants, and exists for the same reason: a
launcher can go stale without any test noticing. Text assertions are used only
where the invariant genuinely is the text; where the bug was behavioural, the test
runs the real script against a stub interpreter.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_MIGRATIONS = REPO_ROOT / "scripts" / "_check_migrations.sh"
RUN_BACKEND = REPO_ROOT / "scripts" / "run_backend.sh"

# ``showmigrations --list`` indents every row with one space. This fixture is the
# shape that made the original anchored grep useless: a database that IS behind,
# written exactly as Django writes it.
APPLIED = "core\n [X] 0001_initial\n [X] 0002_other\n"
BEHIND = "core\n [X] 0001_initial\n [ ] 0002_add_column\n"


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)


bash_required = unittest.skipUnless(shutil.which("bash") is not None, "bash is required")


class CheckMigrationsGuardTests(SimpleTestCase):
    """The guard exists to fail a start whose schema the code does not have.

    It printed "Migrations: all applied" against exactly the four-migrations-behind
    database it was written for, because ``grep '^\\[ \\]'`` anchors at column zero
    and Django indents every row. These run the real script against a stub
    interpreter: a text assertion on the grep pattern would itself be a text
    assertion, and the point is the verdict, not the spelling.
    """

    def _run(self, *, show_output: str, show_exit: int, check_exit: int) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "python"
            # The fixture goes to stdout, which is what the caller captures into
            # $output. Writing it anywhere else leaves the guard with an empty list
            # to report and makes the test assert on its own stub instead.
            _write_stub(
                stub,
                f"""
case "$*" in
  *showmigrations*) printf '%s' {show_output!r}; exit {show_exit} ;;
  *migrate*--check*) exit {check_exit} ;;
esac
exit 0
""",
            )
            env = {**os.environ, "PYTHON_BIN": str(stub), "PROJECT_ROOT": str(root)}
            return subprocess.run(
                ["bash", str(CHECK_MIGRATIONS)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

    @bash_required
    def test_an_unapplied_migration_fails_the_guard(self):
        result = self._run(show_output=BEHIND, show_exit=0, check_exit=1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Pending migrations", result.stderr)
        self.assertIn("0002_add_column", result.stderr)

    @bash_required
    def test_a_fully_applied_database_passes(self):
        result = self._run(show_output=APPLIED, show_exit=0, check_exit=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all applied", result.stdout)

    @bash_required
    def test_an_unreachable_database_warns_and_passes(self):
        """Connectivity is not a schema question; service probing owns that.

        Without this rule a network blip would present as a migration failure and
        send the developer to ``migrate`` against a database that is fine.
        """
        result = self._run(show_output="", show_exit=1, check_exit=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("could not read state", result.stderr)


class BackendLauncherHandoffTests(SimpleTestCase):
    """``run_backend.sh`` must reach ``exec runserver``.

    It sourced ``_native_env.sh``, which assigned PYTHON_BIN and PROJECT_ROOT as
    shell-local variables, then invoked ``_check_migrations.sh`` as a CHILD bash.
    Unexported assignments stop at the process boundary, the child's
    ``${PYTHON_BIN:?}`` aborted it, the caller's ``set -e`` aborted with it, and
    the backend never started -- on every run, not only after a reboot. This is
    the reproduction, run against the real launcher with the interpreter stubbed.
    """

    @bash_required
    def test_the_pre_flight_handoff_reaches_the_server_exec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = root / "argv.txt"
            stub = root / "python"
            _write_stub(
                stub,
                f"""
printf '%s\\n' "$*" >> "{argv}"
case "$*" in
  *showmigrations*) printf 'core\\n [X] 0001_initial\\n'; exit 0 ;;
  *migrate*--check*) exit 0 ;;
esac
exit 0
""",
            )
            # _native_env.sh also refuses to run without an executable CELERY_BIN,
            # which CI has no .venv to provide. Stub both or the launcher dies before
            # the handoff this test exists to prove.
            env = {**os.environ, "PYTHON_BIN": str(stub), "CELERY_BIN": str(stub)}
            env.pop("DJANGO_BIND", None)
            result = subprocess.run(
                ["bash", str(RUN_BACKEND)],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Read here: the TemporaryDirectory is gone the moment this block ends,
            # and a record read after it silently looks like "no calls at all",
            # which fails as a launcher bug rather than as a broken test.
            calls = argv.read_text(encoding="utf-8").splitlines() if argv.exists() else []

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            any("manage.py runserver" in call for call in calls),
            f"runserver was never exec'd; the launcher died earlier. Calls seen: {calls}"
            f"\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )


class LocalStackTeardownTests(SimpleTestCase):
    """Orphans survive a restart because teardown signalled the wrong pid.

    ``start_service`` used to record the pid of its own log-pipeline subshell and
    ``cleanup`` killed only that, so npm's node, runserver's autoreload child and
    every Celery prefork worker were reparented to init and kept serving. The next
    start then found 5173 held, Vite's ``strictPort`` aborted it within a second,
    and ``wait -n`` tore the whole new stack down -- which is how a dead backend
    got misread as a dashboard failure.

    Each assertion here is one invariant whose silent loss brings that back, and
    nothing more: brittle text assertions on a shell script are their own
    maintenance cost.
    """

    SCRIPT = REPO_ROOT / "scripts" / "run_local_stack.sh"

    @property
    def text(self) -> str:
        return self.SCRIPT.read_text(encoding="utf-8")

    def test_services_start_in_their_own_process_group(self):
        self.assertIn("setsid bash", self.text)

    def test_the_wrapper_preserves_the_service_exit_status(self):
        # Without -o pipefail a `bash -c` child reports its last pipeline element,
        # which is the prefixer and always exits 0, so every crash said "status 0".
        self.assertRegex(self.text, r"setsid bash -o pipefail")

    def test_cleanup_signals_a_process_group_not_a_recorded_pid(self):
        # Must not degrade back to deriving the pgid from the recorded pid: that
        # pid is the group leader, which dies first, and the lookup then returns
        # nothing for the very services that are still running.
        self.assertRegex(self.text, r'kill "-\$sig" -- "-\$pid"')

    def test_teardown_asks_the_group_not_the_leader_about_liveness(self):
        self.assertIn("group_alive", self.text)

    def test_cleanup_never_signals_its_own_group(self):
        self.assertIn("SELF_PGID", self.text)

    def test_the_frontend_cannot_tear_down_the_stack(self):
        self.assertRegex(self.text, r"frontend")
        self.assertIn("is_optional", self.text)

    def test_a_held_port_is_refused_before_anything_starts(self):
        self.assertIn("port_holders", self.text)
        self.assertIn("STACK_RECLAIM_PORTS", self.text)


class NativeEnvExportTests(SimpleTestCase):
    """The variables child launchers assert on must cross the process boundary."""

    SCRIPT = REPO_ROOT / "scripts" / "_native_env.sh"

    def test_python_bin_and_project_root_are_exported(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        self.assertRegex(text, r"export[^\n]*\bPYTHON_BIN\b")
        self.assertRegex(text, r"export[^\n]*\bPROJECT_ROOT\b")
