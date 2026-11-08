"""The dedupe command for pre-2026-11-02 TechnicalIndicator rows.

The pair this must remove: one session date, two instants eight hours apart.
``session_key_timestamp(d)`` is ``d 00:00+08``; the pre-flip convention was ``d 00:00Z``,
which is that plus eight hours. ``unique_together`` includes ``timestamp``, so both rows
are legal and a recompute created the second instead of updating the first.

The case that must NOT be deleted: a lone old-convention row with no new-convention twin.
It is the only record for that session, and deleting it would lose data the backfill has
not rewritten yet.
"""

from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import session_key_timestamp
from apps.markets.models import Asset, Market

DAY = date(2026, 11, 3)
NEW_KEY = session_key_timestamp(DAY)
OLD_KEY = NEW_KEY + timedelta(hours=8)


class DedupeSessionKeysTests(TestCase):
    def setUp(self):
        market = Market.objects.create(code='CN', name='China')
        self.asset = Asset.objects.create(
            market=market, symbol='600000', ts_code='600000.SH', name='A0',
            list_date=date(2015, 1, 1),
        )

    def _row(self, timestamp, indicator_type='RSI', parameters='14', value=50, asset=None):
        return TechnicalIndicator.objects.create(
            asset=asset or self.asset, timestamp=timestamp,
            indicator_type=indicator_type, parameters=parameters, value=value,
        )

    def _run(self, *args):
        out = StringIO()
        call_command('dedupe_technical_indicator_session_keys', *args, stdout=out)
        return out.getvalue()

    def test_the_old_convention_row_is_deleted_and_the_new_one_kept(self):
        new = self._row(NEW_KEY)
        old = self._row(OLD_KEY, value=999999)

        self._run('--start-date', DAY.isoformat())

        remaining = list(TechnicalIndicator.objects.values_list('id', 'value'))
        self.assertEqual(remaining, [(new.pk, 50)])
        self.assertFalse(TechnicalIndicator.objects.filter(pk=old.pk).exists())

    def test_a_lone_old_convention_row_survives(self):
        old = self._row(OLD_KEY)
        self._run('--start-date', DAY.isoformat())
        self.assertTrue(TechnicalIndicator.objects.filter(pk=old.pk).exists())

    def test_a_lone_new_convention_row_survives(self):
        new = self._row(NEW_KEY)
        self._run('--start-date', DAY.isoformat())
        self.assertTrue(TechnicalIndicator.objects.filter(pk=new.pk).exists())

    def test_a_dry_run_deletes_nothing(self):
        self._row(NEW_KEY)
        self._row(OLD_KEY)
        output = self._run('--start-date', DAY.isoformat(), '--dry-run')
        self.assertEqual(TechnicalIndicator.objects.count(), 2)
        self.assertIn('1 duplicate row(s) would be deleted', output)

    def test_different_indicator_types_are_not_confused(self):
        self._row(NEW_KEY, indicator_type='RSI')
        old_rsi = self._row(OLD_KEY, indicator_type='RSI')
        old_macd = self._row(OLD_KEY, indicator_type='MACD')

        self._run('--start-date', DAY.isoformat())

        # RSI has a twin, so its old row goes. MACD has none, so its old row is alone.
        self.assertFalse(TechnicalIndicator.objects.filter(pk=old_rsi.pk).exists())
        self.assertTrue(TechnicalIndicator.objects.filter(pk=old_macd.pk).exists())

    def test_different_parameters_are_not_confused(self):
        self._row(NEW_KEY, parameters='14')
        old_14 = self._row(OLD_KEY, parameters='14')
        old_20 = self._row(OLD_KEY, parameters='20')

        self._run('--start-date', DAY.isoformat())

        self.assertFalse(TechnicalIndicator.objects.filter(pk=old_14.pk).exists())
        self.assertTrue(TechnicalIndicator.objects.filter(pk=old_20.pk).exists())

    def test_two_different_sessions_are_independent(self):
        next_day = DAY + timedelta(days=1)
        self._row(NEW_KEY)
        self._row(NEW_KEY + timedelta(hours=8))
        # The next session's new key happens to equal this session's old key + 16h; only
        # the exact pair for each session may be deleted.
        survivor = self._row(session_key_timestamp(next_day))

        self._run('--start-date', DAY.isoformat())

        self.assertTrue(TechnicalIndicator.objects.filter(pk=survivor.pk).exists())
        self.assertEqual(TechnicalIndicator.objects.count(), 2)

    def test_a_small_batch_size_still_deletes_everything(self):
        pairs = []
        for day_offset in range(5):
            day = DAY + timedelta(days=day_offset)
            self._row(session_key_timestamp(day))
            pairs.append(self._row(session_key_timestamp(day) + timedelta(hours=8)))

        self._run('--start-date', DAY.isoformat(), '--batch-size', '1')

        self.assertEqual(TechnicalIndicator.objects.count(), 5)
        self.assertFalse(TechnicalIndicator.objects.filter(
            pk__in=[row.pk for row in pairs]).exists())

    def test_dict_parameters_are_compared_by_value(self):
        """``parameters`` is a JSONField: real rows carry a dict, which is unhashable."""
        self._row(NEW_KEY, indicator_type='MACD', parameters={'fast': 12, 'slow': 26})
        old = self._row(OLD_KEY, indicator_type='MACD', parameters={'slow': 26, 'fast': 12})
        other = self._row(OLD_KEY, indicator_type='MACD', parameters={'fast': 5, 'slow': 35})

        self._run('--start-date', DAY.isoformat())

        # Same JSON in a different key order is the same key, so its old row goes.
        self.assertFalse(TechnicalIndicator.objects.filter(pk=old.pk).exists())
        # A different parameter set has no twin and must survive.
        self.assertTrue(TechnicalIndicator.objects.filter(pk=other.pk).exists())

    def test_rows_before_the_start_date_are_left_alone(self):
        earlier = DAY - timedelta(days=10)
        self._row(session_key_timestamp(earlier))
        old = self._row(session_key_timestamp(earlier) + timedelta(hours=8))

        self._run('--start-date', DAY.isoformat())

        self.assertTrue(TechnicalIndicator.objects.filter(pk=old.pk).exists())