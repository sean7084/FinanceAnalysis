"""The zone-stable TechnicalIndicator write key, and the check that guards it.

The defect: every indicator write keyed on ``make_aware(combine(trade_date, midnight))``,
which takes its zone from ``settings.TIME_ZONE``. When that setting flipped from UTC to
Asia/Shanghai on 2026-11-02, the same trade date started producing a *different instant*
-- midnight Beijing is eight hours earlier than midnight UTC -- so a recompute inserted a
parallel row instead of updating the existing one. ``unique_together`` includes
``timestamp``, so it cannot catch that.

The read side is unaffected and must stay as it is: ``session_midnight`` pairs with the
``timestamp__date`` cast precisely because both resolve through the same setting.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.core.checks import TIME_ZONE_CHECK_ID
from apps.core.date_floor import session_key_timestamp, session_midnight
from apps.core.timez import SESSION_TZ, SESSION_TZ_NAME

UTC = ZoneInfo('UTC')
DAY = date(2026, 10, 30)


class SessionKeyTimestampTests(SimpleTestCase):
    def test_it_is_midnight_beijing(self):
        key = session_key_timestamp(DAY)
        self.assertTrue(timezone.is_aware(key))
        self.assertEqual(key.astimezone(SESSION_TZ).time(), datetime.min.time())
        self.assertEqual(key.astimezone(SESSION_TZ).date(), DAY)
        self.assertEqual(key.utcoffset(), timedelta(hours=8))

    def test_it_does_not_move_when_time_zone_changes(self):
        """The regression pin. ``session_midnight`` DOES move; that asymmetry is the point."""
        with override_settings(TIME_ZONE='Asia/Shanghai'):
            beijing = session_key_timestamp(DAY)
            beijing_read = session_midnight(DAY)
        with override_settings(TIME_ZONE='UTC'):
            utc = session_key_timestamp(DAY)
            utc_read = session_midnight(DAY)

        self.assertEqual(beijing, utc)
        self.assertEqual(beijing.astimezone(UTC), datetime(2026, 10, 29, 16, 0, tzinfo=UTC))

        # Documented asymmetry: the read helper follows the setting and must keep doing so,
        # because its whole job is to match a timestamp__date cast that also follows it.
        self.assertNotEqual(beijing_read, utc_read)
        self.assertEqual(beijing_read.astimezone(SESSION_TZ).hour, 0)
        self.assertEqual(utc_read.utcoffset(), timedelta(0))

    def test_a_datetime_or_timestamp_uses_only_its_date(self):
        moment = datetime(2026, 10, 30, 21, 45, tzinfo=SESSION_TZ)
        self.assertEqual(session_key_timestamp(moment), session_key_timestamp(DAY))

    def test_no_write_key_is_built_from_the_mutable_setting_any_more(self):
        """Source scan: write keys must go through the helper.

        Two sites legitimately keep ``make_aware(datetime.combine(...))`` because they are
        *query bounds*, not keys: ``catchup/replay.py`` bounds ``last_run_at`` and
        ``factors/tasks.py`` bounds a ``timestamp__lt`` filter. Both pair with a range
        comparison and must follow the setting, exactly like ``session_midnight``. They
        are listed here so a new occurrence fails rather than blending in.
        """
        from pathlib import Path

        read_bounds = {
            'apps/catchup/replay.py',
            'apps/factors/tasks.py',
        }
        root = Path(__file__).resolve().parents[2]
        offenders = []
        for path in (root / 'apps').rglob('*.py'):
            if path.name.startswith('tests') or path.name == 'date_floor.py':
                continue
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding='utf-8')
            for needle in ('make_aware(pd.Timestamp.combine(',
                           'make_aware(datetime.combine('):
                if needle in text and relative not in read_bounds:
                    offenders.append(f'{relative}: {needle}')
        self.assertEqual(offenders, [], 'a setting-dependent write key came back')


class CheckRegistrationTests(SimpleTestCase):
    def test_the_check_is_registered(self):
        """Django does not import an app's ``checks`` module on its own.

        Asserting on the behaviour rather than on the registry's internals: if the module
        is never imported, ``run_checks`` returns nothing here.
        """
        with override_settings(TIME_ZONE='UTC'):
            ids = [error.id for error in run_checks() if error.id == TIME_ZONE_CHECK_ID]
        self.assertEqual(ids, [TIME_ZONE_CHECK_ID])

    def test_it_is_silent_when_the_zones_agree(self):
        with override_settings(TIME_ZONE=SESSION_TZ_NAME):
            ids = [e.id for e in run_checks() if e.id == TIME_ZONE_CHECK_ID]
        self.assertEqual(ids, [])

    def test_it_fires_when_the_zones_disagree(self):
        with override_settings(TIME_ZONE='America/New_York'):
            errors = [e for e in run_checks() if e.id == TIME_ZONE_CHECK_ID]
        self.assertEqual(len(errors), 1)
        self.assertIn('America/New_York', errors[0].msg)
        self.assertIn('unique_together', errors[0].hint)


class WriteKeyIdempotenceTests(TestCase):
    """The behaviour the old key broke: a recompute must update, not insert."""

    def setUp(self):
        from apps.markets.models import Asset, Market

        market = Market.objects.create(code='CN', name='China')
        self.asset = Asset.objects.create(
            market=market, symbol='600000', ts_code='600000.SH', name='A0',
            list_date=date(2015, 1, 1),
        )

    def _upsert(self, timestamp):
        from apps.analytics.models import TechnicalIndicator

        return TechnicalIndicator.objects.get_or_create(
            asset=self.asset, timestamp=timestamp, indicator_type='RSI',
            parameters='14', defaults={'value': 50},
        )

    def test_the_same_trade_date_is_one_row_across_a_time_zone_change(self):
        from apps.analytics.models import TechnicalIndicator

        with override_settings(TIME_ZONE='UTC'):
            first, created_first = self._upsert(session_key_timestamp(DAY))
        with override_settings(TIME_ZONE='Asia/Shanghai'):
            second, created_second = self._upsert(session_key_timestamp(DAY))

        self.assertTrue(created_first)
        self.assertFalse(created_second, 'the recompute inserted a parallel row')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TechnicalIndicator.objects.filter(asset=self.asset).count(), 1)

    def test_the_old_setting_dependent_key_would_have_duplicated(self):
        """Pins the mechanism, not just the fix: the two conventions differ by 8h."""
        from apps.analytics.models import TechnicalIndicator

        with override_settings(TIME_ZONE='UTC'):
            old_style = session_midnight(DAY)
        with override_settings(TIME_ZONE='Asia/Shanghai'):
            new_style = session_key_timestamp(DAY)
        self.assertNotEqual(old_style, new_style)
        self.assertEqual(new_style - old_style, timedelta(hours=-8))

        TechnicalIndicator.objects.create(
            asset=self.asset, timestamp=new_style, indicator_type='RSI',
            parameters='14', value=50,
        )
        # The old key is a different instant, so unique_together does not stop it.
        TechnicalIndicator.objects.create(
            asset=self.asset, timestamp=old_style, indicator_type='RSI',
            parameters='14', value=50,
        )
        self.assertEqual(
            TechnicalIndicator.objects.filter(
                asset=self.asset, indicator_type='RSI', parameters='14').count(), 2)