"""The per-day indicator prefetch must return exactly what the per-asset read returns.

The risk this module exists to close is predicate drift: `_matching_indicator_rows` admits a
candidate row when its `parameters` match the requested key/value OR its parameters are empty
OR NULL, and then a Python scoring pass prefers the exact match. A hand-written SQL version of
that filter that only tests "the key exists" returns the same ROW COUNT while returning
different ROWS -- which is how an equivalence check can pass for the wrong reason. These tests
put a wrong-value row and an empty-parameters row in the same asset's window, where count and
content separate.

`ArraySubquery` is PostgreSQL-only, so the priming tests run on the project's test database.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import models
from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.markets.models import Asset, Market
from apps.prediction import historical_features as hf


class _Base(TestCase):
    def setUp(self):
        market = Market.objects.create(code='CN', name='China')
        self.asset = Asset.objects.create(
            market=market, symbol='600100', ts_code='600100.SH', name='Prefetch',
            list_date=date(2015, 1, 1),
        )
        self.other = Asset.objects.create(
            market=market, symbol='600101', ts_code='600101.SH', name='Other',
            list_date=date(2015, 1, 1),
        )
        self.as_of = date(2026, 3, 20)
        hf.INDICATOR_PREFETCH_STATS.update({"installed": 0, "cache_hit": 0, "fallback": 0})

    def add(self, asset, day, value, parameters):
        return TechnicalIndicator.objects.create(
            asset=asset,
            indicator_type='RSI',
            value=Decimal(value),
            parameters=parameters,
            timestamp=timezone.make_aware(datetime.combine(day, time(15, 0))),
        )


class CandidateDepthTests(TestCase):
    def test_default_read_depth_is_twelve_rows(self):
        self.assertEqual(hf._candidate_take(1), 12)

    def test_depth_scales_with_limit_and_keeps_the_floor(self):
        self.assertEqual(hf._candidate_take(4), 16)
        self.assertEqual(hf._candidate_take(0), 12)


class PrefetchEquivalenceTests(_Base):
    def _rows(self, asset, cache=None):
        return hf._matching_indicator_rows(
            asset.id,
            self.as_of,
            'RSI',
            expected_parameters={'timeperiod': 14},
            required_keys=('timeperiod',),
            parameter_key='timeperiod',
            parameter_value=14,
            limit=1,
            cache=cache,
        )

    def test_primed_candidate_ids_equal_the_per_asset_query_ordered_ids(self):
        take = hf._candidate_take(1)
        direct = list(
            hf._indicator_candidate_queryset(
                models.Q(asset_id=self.asset.id),
                self.as_of, 'RSI', 'timeperiod', 14, take,
            )
        )
        cache = {}
        hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        primed = cache[hf._candidate_cache_key(self.asset.id, self.as_of, 'RSI', 'timeperiod', 14, take)]
        self.assertEqual([row.pk for row in primed], [row.pk for row in direct])

    def test_wrong_value_rows_are_excluded_rather_than_merely_counted(self):
        """A row carrying timeperiod=20 must not enter a timeperiod=14 candidate list.

        Row counts alone would not notice: without this row the asset returns 12 candidates,
        with it wrongly included it still returns 12. The predicate has to be right, not merely
        the same size.
        """
        keep = self.add(self.asset, self.as_of, '65.0', {'timeperiod': 14})
        for offset in range(1, 14):
            self.add(self.asset, self.as_of - timedelta(days=offset), '50.0', {'timeperiod': 20})

        cache = {}
        hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        primed = cache[hf._candidate_cache_key(self.asset.id, self.as_of, 'RSI', 'timeperiod', 14, hf._candidate_take(1))]
        self.assertIn(keep.pk, [row.pk for row in primed])
        self.assertEqual(
            [row.parameters['timeperiod'] for row in primed if row.parameters],
            [14] * len([row for row in primed if row.parameters]),
        )

    def test_empty_parameter_rows_stay_admissible_as_they_were(self):
        """The compatibility filter has always let `parameters={}` through for scoring."""
        blank = self.add(self.asset, self.as_of, '61.0', {})
        cache = {}
        hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        primed = cache[hf._candidate_cache_key(self.asset.id, self.as_of, 'RSI', 'timeperiod', 14, hf._candidate_take(1))]
        self.assertIn(blank.pk, [row.pk for row in primed])

    def test_scoring_result_is_identical_primed_or_not(self):
        exact = self.add(self.asset, self.as_of, '70.0', {'timeperiod': 14})
        self.add(self.asset, self.as_of, '10.0', {})

        unprimed = self._rows(self.asset)
        cache = {}
        hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        primed = self._rows(self.asset, cache=cache)
        self.assertEqual([row.pk for row in primed], [row.pk for row in unprimed])
        self.assertEqual([row.pk for row in primed], [exact.pk])

    def test_priming_covers_every_asset_in_the_request(self):
        self.add(self.other, self.as_of, '44.0', {'timeperiod': 14})
        cache = {}
        primed = hf.prime_indicator_candidates(
            [self.asset.id, self.other.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache
        )
        self.assertEqual(primed, 2)
        take = hf._candidate_take(1)
        for asset in (self.asset, self.other):
            self.assertIn(hf._candidate_cache_key(asset.id, self.as_of, 'RSI', 'timeperiod', 14, take), cache)

    def test_an_asset_without_rows_is_primed_with_an_empty_list_not_skipped(self):
        """Silently skipping an empty asset would leave it to the fallback path forever."""
        cache = {}
        primed = hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        self.assertEqual(primed, 1)
        key = hf._candidate_cache_key(self.asset.id, self.as_of, 'RSI', 'timeperiod', 14, hf._candidate_take(1))
        self.assertEqual(cache[key], [])

    def test_the_empty_list_is_read_back_as_no_rows_rather_than_a_fresh_query(self):
        self.add(self.asset, self.as_of, '70.0', {'timeperiod': 14})
        cache = {}
        hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        before = hf.INDICATOR_PREFETCH_STATS['fallback']
        self.assertTrue(self._rows(self.asset, cache=cache))
        self.assertEqual(hf.INDICATOR_PREFETCH_STATS['fallback'], before)


class PrefetchStatsTests(_Base):
    def test_unprimed_reads_count_as_fallbacks(self):
        self.add(self.asset, self.as_of, '70.0', {'timeperiod': 14})
        before = hf.INDICATOR_PREFETCH_STATS['fallback']
        self._rows_with_cache(None)
        self.assertEqual(hf.INDICATOR_PREFETCH_STATS['fallback'], before + 1)

    def _rows_with_cache(self, cache):
        return hf._matching_indicator_rows(
            self.asset.id, self.as_of, 'RSI',
            expected_parameters={'timeperiod': 14}, required_keys=('timeperiod',),
            parameter_key='timeperiod', parameter_value=14, limit=1, cache=cache,
        )

    def test_primed_reads_are_served_from_the_installed_entry(self):
        """`installed` alone could be satisfied by a prefetch nobody reads.

        The property that matters is the read hitting the installed list, so this asserts the
        cache_hit counter moves and the fallback counter does not.
        """
        self.add(self.asset, self.as_of, '70.0', {'timeperiod': 14})
        cache = {}
        hf.prime_indicator_candidates([self.asset.id], self.as_of, (('RSI', 'timeperiod', 14, 1),), cache)
        installed_after_priming = hf.INDICATOR_PREFETCH_STATS["installed"]
        self.assertEqual(installed_after_priming, 1)
        hits_before = hf.INDICATOR_PREFETCH_STATS["cache_hit"]
        fallbacks_before = hf.INDICATOR_PREFETCH_STATS['fallback']
        self._rows_with_cache(cache)
        self.assertEqual(hf.INDICATOR_PREFETCH_STATS["cache_hit"], hits_before + 1)
        self.assertEqual(hf.INDICATOR_PREFETCH_STATS['fallback'], fallbacks_before)
        self.assertEqual(hf.INDICATOR_PREFETCH_STATS["installed"], installed_after_priming)

    def test_no_request_no_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(hf.prime_indicator_candidates([], self.as_of, (('RSI', 'timeperiod', 14, 1),), {}), 0)
            self.assertEqual(hf.prime_indicator_candidates([self.asset.id], self.as_of, (), {}), 0)
        self.assertEqual(len(captured.captured_queries), 0)
