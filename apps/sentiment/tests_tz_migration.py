"""Tests for the sentiment 0003 legacy-UTC correction.

Three kinds of ``NewsArticle.published_at`` row exist around the timezone flip
and the migration must tell them apart from the data itself, not from a
timestamp cutoff:

* **legacy mis-stamped** — the provider's Beijing wall clock stored as though it
  were UTC. This is the only kind that has to move.
* **post-flip correct** — the same wall clock string stored with a +08:00 offset.
  It can never equal the UTC reading, so it excludes itself.
* **``timezone.now()`` fallback** — a true instant, no comparable raw string.

The functions under test take the model class, so they are called here with the
real ``NewsArticle`` rather than through ``MigrationTestCase``; the migration
file itself is a two-line ``RunPython`` wrapper and ``makemigrations --check``
already covers its shape.
"""

import io
from contextlib import redirect_stdout
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.sentiment.migrations._tz_shift_logic import (
    MARKER_KEY,
    MARKER_VALUE,
    _restore_marked_rows,
    _shift_legacy_rows,
)
from apps.sentiment.models import NewsArticle

BEIJING = ZoneInfo('Asia/Shanghai')

# The provider's own wall clock, as it arrives in ``metadata['raw']``.
WALL = '2026-03-05 09:30:00'
LEGACY_MIS_STAMPED = datetime(2026, 3, 5, 9, 30, tzinfo=UTC)
POST_FLIP_CORRECT = datetime(2026, 3, 5, 9, 30, tzinfo=BEIJING)


class TzShiftMigrationTests(TestCase):
    def _seed(self):
        """One row of each kind, plus the two ways a row can fail to match."""
        self.legacy = self._article('legacy', 'https://example.com/legacy',
                                    LEGACY_MIS_STAMPED, {'raw': {'published_at': WALL}})
        self.flipped = self._article('flipped', 'https://example.com/flipped',
                                     POST_FLIP_CORRECT, {'raw': {'published_at': WALL}})
        self.fallback = self._article('fallback', 'https://example.com/fallback',
                                      timezone.now(), {'raw': {'title': 'no time key'}})
        self.repaired = self._article('repaired', 'https://example.com/repaired',
                                      timezone.now(), {'raw': {'published_at': WALL}})

    def _article(self, slug, url, published_at, metadata):
        return NewsArticle.objects.create(
            source=NewsArticle.Source.EASTMONEY,
            title=f'article {slug}',
            url=url,
            published_at=published_at,
            metadata=metadata,
        )

    def _forward(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            counted = _shift_legacy_rows(NewsArticle)
        return counted, buffer.getvalue()

    def _reverse(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            reverted = _restore_marked_rows(NewsArticle)
        return reverted, buffer.getvalue()

    def _reload(self, article):
        return NewsArticle.objects.get(pk=article.pk)

    def setUp(self):
        self._seed()

    def test_forward_shifts_only_the_legacy_rows_by_eight_hours(self):
        counted, report = self._forward()

        self.assertEqual(counted['shifted'], 1)
        self.assertEqual(counted['mismatch_untouched'], 2)
        self.assertEqual(counted['no_raw_payload'], 1)
        self.assertEqual(counted['total_scanned'], 4)

        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.published_at, POST_FLIP_CORRECT)
        self.assertEqual(self.legacy.metadata[MARKER_KEY], MARKER_VALUE)

        for article in (self.flipped, self.fallback, self.repaired):
            unchanged = self._reload(article)
            self.assertEqual(unchanged.published_at, article.published_at)
            self.assertNotIn(MARKER_KEY, unchanged.metadata)

    def test_the_row_count_report_is_printed_for_the_deploy_log(self):
        _, report = self._forward()
        self.assertIn('shifted=1', report)
        self.assertIn('already_marked=0', report)
        self.assertIn('mismatch_untouched=2', report)
        self.assertIn('EASTMONEY=1', report)

    def test_forward_is_idempotent(self):
        first, _ = self._forward()
        second, _ = self._forward()

        self.assertEqual(second['shifted'], 0)
        self.assertEqual(second['already_marked'], first['shifted'])

        moved = self._reload(self.legacy).published_at
        self.assertEqual(moved, POST_FLIP_CORRECT)

    def test_reverse_restores_the_original_instant_and_clears_the_marker(self):
        original = self._reload(self.legacy).published_at
        untouched = {a.pk: self._reload(a).published_at
                     for a in (self.flipped, self.fallback, self.repaired)}

        self._forward()
        reverted, report = self._reverse()

        self.assertEqual(reverted, 1)
        self.assertIn('reverted=1', report)

        restored = self._reload(self.legacy)
        self.assertEqual(restored.published_at, original)
        self.assertNotIn(MARKER_KEY, restored.metadata)
        for pk, moment in untouched.items():
            self.assertEqual(self._reload(NewsArticle.objects.get(pk=pk)).published_at, moment)

    def test_reverse_is_a_noop_when_nothing_was_marked(self):
        reverted, _ = self._reverse()
        self.assertEqual(reverted, 0)
        self.assertEqual(self._reload(self.legacy).published_at, LEGACY_MIS_STAMPED)

    def test_the_correction_preserves_the_day_bucket_sentiment_scores_key_on(self):
        """Why no ``SentimentScore`` recompute ships with this migration.

        The old bucket was the UTC date of the mis-stamped value; the corrected
        instant's Beijing date is the same day, so every score keyed on that
        date still points at the right article.
        """
        self._forward()
        self.legacy.refresh_from_db()

        self.assertEqual(LEGACY_MIS_STAMPED.date(), self.legacy.published_at.astimezone(BEIJING).date())

    def test_sina_and_tushare_payload_shapes_are_recognised(self):
        """Each provider keeps its timestamp under a different raw key."""
        NewsArticle.objects.all().delete()
        for key in ('published_at', 'time', 'pub_time', 'date'):
            with self.subTest(raw_key=key):
                article = self._article(
                    f'shape-{key}', f'https://example.com/shape-{key}',
                    LEGACY_MIS_STAMPED, {'raw': {key: WALL}})
                counted, _ = self._forward()
                self.assertEqual(counted['shifted'], 1)
                article.refresh_from_db()
                self.assertEqual(article.published_at, POST_FLIP_CORRECT)
                article.delete()

    def test_compact_tushare_dates_are_recognised(self):
        """TuShare publishes ``yyyymmddHHMMSS``, not an ISO string."""
        NewsArticle.objects.all().delete()
        compact = NewsArticle.objects.create(
            source=NewsArticle.Source.EXCHANGE,
            title='compact',
            url='https://example.com/compact',
            published_at=datetime(2026, 3, 5, 9, 30, tzinfo=UTC),
            metadata={'raw': {'pub_time': '20260305T093000'.replace('T', '')}},
        )
        counted, _ = self._forward()
        self.assertEqual(counted['shifted'], 1)
        compact.refresh_from_db()
        self.assertEqual(compact.published_at, POST_FLIP_CORRECT)

    def test_a_row_rewritten_by_hand_is_left_alone(self):
        """An editor who already fixed a row must not be shifted a second time."""
        NewsArticle.objects.all().delete()
        already_fixed = self._article('fixed', 'https://example.com/fixed-row',
                                      POST_FLIP_CORRECT, {'raw': {'published_at': WALL}})
        counted, _ = self._forward()
        self.assertEqual(counted['shifted'], 0)
        already_fixed.refresh_from_db()
        self.assertEqual(already_fixed.published_at, POST_FLIP_CORRECT)
