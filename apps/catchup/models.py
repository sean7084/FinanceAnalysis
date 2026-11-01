"""What a catch-up pass found and did about it, one row per (series, attempt).

`units_remaining` is reporting, not state: the detectors are data-level, so the
database is itself the checkpoint and the next run re-detects whatever is left.
"""

from django.db import models


class CatchUpRun(models.Model):
    class Outcome(models.TextChoices):
        OK = 'OK', 'Repaired'
        PARTIAL = 'PARTIAL', 'Partially repaired'
        NO_GAP = 'NO_GAP', 'No gap found'
        BLOCKED_UPSTREAM = 'BLOCKED_UPSTREAM', 'Blocked by an upstream gap'
        SKIPPED = 'SKIPPED', 'Skipped'
        FAILED = 'FAILED', 'Failed'

    class Cause(models.TextChoices):
        BEAT_OUTAGE = 'beat_outage', 'Beat was down'
        TASK_FAILURE = 'task_failure', 'Task failed after dispatch'
        PROVIDER_GAP = 'provider_gap', 'Provider returned no data'
        THROTTLED = 'throttled', 'TuShare quota exhausted'
        ENTITLEMENT = 'entitlement', 'Denied on this token tier'
        UNKNOWN = 'unknown', 'Unknown'

    class Trigger(models.TextChoices):
        STARTUP = 'startup', 'Cold-stack startup hook'
        BEAT = 'beat', 'Hourly beat entry'
        MANUAL = 'manual', 'manage.py catch_up'

    # Outcomes that mean "nothing happened", so an hourly tick across sixteen
    # series would otherwise write hundreds of identical rows a day. Everything
    # else is evidence and always inserts.
    DEDUPED_OUTCOMES = frozenset({Outcome.NO_GAP, Outcome.SKIPPED})

    series = models.CharField(max_length=64, db_index=True)
    triggered_by = models.CharField(max_length=16, choices=Trigger.choices)
    outcome = models.CharField(max_length=24, choices=Outcome.choices)
    cause = models.CharField(max_length=24, choices=Cause.choices, blank=True, default='')
    gap_start = models.DateField(null=True, blank=True)
    gap_end = models.DateField(null=True, blank=True)
    repaired_dates = models.JSONField(default=list, blank=True)
    units_done = models.PositiveIntegerField(default=0)
    units_remaining = models.PositiveIntegerField(default=0)
    estimated_calls = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True, default='')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['series', '-started_at']),
            models.Index(fields=['triggered_by', '-started_at']),
        ]

    def __str__(self):
        return f'{self.series} {self.outcome} {self.gap_start}..{self.gap_end}'
