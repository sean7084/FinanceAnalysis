"""The sargable indicator bound must select exactly the rows the date cast selected.

Speed is not the claim here -- equivalence is. The repo has already been bitten by a
"same count, different contents" prefetch (#139), so the guard compares id sets across
both session-key conventions rather than trusting the arithmetic.
"""
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import session_key_timestamp, session_midnight
from apps.markets.models import Asset, Market
from apps.prediction.historical_features import (
    _indicator_candidate_queryset,
    _session_upper_bound,
)
from django.db.models import Q


class SargableIndicatorBoundTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='CN', name='China')
        cls.asset = Asset.objects.create(
            market=cls.market, symbol='600100', ts_code='600100.SH', name='Sargable',
            list_date=date(2015, 1, 1),
        )
        cls.as_of = date(2026, 11, 6)
        cls.rows = {}
        for offset in (-1, 0, 1):
            day = cls.as_of + timedelta(days=offset)
            # Old convention: UTC midnight. New convention: SESSION_TZ midnight, which is
            # eight hours earlier as an instant. Both must resolve to the same trade date.
            for label, stamp in (
                ('utc', datetime.combine(day, time.min, tzinfo=dt_timezone.utc)),
                ('session', session_key_timestamp(day)),
            ):
                row = TechnicalIndicator.objects.create(
                    asset=cls.asset, timestamp=stamp, indicator_type='SMA',
                    value=Decimal('1.00000000'), parameters={'timeperiod': 50},
                )
                cls.rows[(offset, label)] = row.pk

    def test_bound_matches_date_cast_across_both_write_conventions(self):
        cast_ids = set(
            TechnicalIndicator.objects.filter(
                asset_id=self.asset.pk, indicator_type='SMA',
                timestamp__date__lte=self.as_of,
            ).values_list('pk', flat=True)
        )
        bound_ids = set(
            TechnicalIndicator.objects.filter(
                asset_id=self.asset.pk, indicator_type='SMA',
                timestamp__lt=_session_upper_bound(self.as_of),
            ).values_list('pk', flat=True)
        )
        self.assertEqual(cast_ids, bound_ids)
        self.assertEqual(cast_ids, {
            self.rows[(-1, 'utc')], self.rows[(-1, 'session')],
            self.rows[(0, 'utc')], self.rows[(0, 'session')],
        })
        self.assertNotIn(self.rows[(1, 'utc')], bound_ids)
        self.assertNotIn(self.rows[(1, 'session')], bound_ids)

    def test_accepts_datetime_as_of(self):
        as_datetime = datetime.combine(self.as_of, time(15, 30), tzinfo=dt_timezone.utc)
        self.assertEqual(_session_upper_bound(as_datetime), _session_upper_bound(self.as_of))

    def test_candidate_queryset_no_longer_wraps_the_column(self):
        captured = []
        original = TechnicalIndicator.objects.filter

        def spy(*args, **kwargs):
            captured.append(kwargs)
            return original(*args, **kwargs)

        with patch.object(TechnicalIndicator.objects, 'filter', side_effect=spy):
            list(_indicator_candidate_queryset(
                Q(asset_id=self.asset.pk), self.as_of, 'SMA', 'timeperiod', 50, 12,
            ))

        self.assertTrue(captured, 'the candidate queryset stopped querying at all')
        for kwargs in captured:
            self.assertNotIn('timestamp__date__lte', kwargs)
            self.assertIn('timestamp__lt', kwargs)

    def test_aware_datetime_in_the_utc_evening_divergence_window(self):
        """20:00 UTC is 04:00 the NEXT day in Asia/Shanghai.

        The ORM ``__date`` lookup converts to the current timezone before taking the date;
        a bare ``.date()`` would be a day short here. The earlier 15:30 UTC test sat outside
        this window and passed against the buggy implementation.
        """
        as_of = datetime.combine(self.as_of, time(20, 0), tzinfo=dt_timezone.utc)
        cast_ids = set(
            TechnicalIndicator.objects.filter(
                asset_id=self.asset.pk, indicator_type='SMA', timestamp__date__lte=as_of,
            ).values_list('pk', flat=True)
        )
        bound_ids = set(
            TechnicalIndicator.objects.filter(
                asset_id=self.asset.pk, indicator_type='SMA',
                timestamp__lt=_session_upper_bound(as_of),
            ).values_list('pk', flat=True)
        )
        self.assertEqual(cast_ids, bound_ids)
        self.assertIn(self.rows[(1, 'session')], bound_ids)
        self.assertIn(self.rows[(1, 'utc')], bound_ids)

    def test_bound_is_the_next_session_midnight(self):
        self.assertEqual(
            _session_upper_bound(self.as_of), session_midnight(self.as_of + timedelta(days=1))
        )