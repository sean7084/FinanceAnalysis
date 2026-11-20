"""B2 / STK-12 (#198): ST derivation state machine and as-of resolution.

The transition matrix is pinned row by row; the filter never reads
listing_status, and batch resolution must equal single-day resolution.
"""
from datetime import date
from unittest.mock import patch

import pandas as pd
from django.test import SimpleTestCase, TestCase, override_settings

from apps.markets.models import Asset, AssetNameChange, AssetSTInterval, Market
from apps.markets.st_history import (
    derive_st_intervals,
    ingest_namechange_records,
    is_st_record,
    rebuild_asset_st_intervals,
    st_asset_ids_as_of,
    st_asset_ids_by_dates,
)


def _row(start, name, reason='', end=None, ann=None):
    return {
        'start_date': date.fromisoformat(start),
        'end_date': date.fromisoformat(end) if end else None,
        'name': name,
        'change_reason': reason,
        'ann_date': date.fromisoformat(ann) if ann else None,
    }


class StRecordPredicateTests(SimpleTestCase):
    def test_st_reasons(self):
        for reason in ('ST', '*ST', '从ST变为*ST', '撤消*ST并实行ST', '退市整理期'):
            self.assertTrue(is_st_record('某股票', reason), reason)

    def test_name_prefixes(self):
        for name in ('ST某股', '*ST某股', 'S*ST某股', 'SST某股'):
            self.assertTrue(is_st_record(name, '其他'), name)

    def test_delisting_suffix(self):
        self.assertTrue(is_st_record('吉艾退', '其他'))

    def test_non_st(self):
        self.assertFalse(is_st_record('平安银行', '其他'))
        self.assertFalse(is_st_record('STOCKHOLDER', '其他'))  # Latin false positive guard


class DeriveIntervalsTests(SimpleTestCase):
    def test_st_to_star_st_merges(self):
        intervals = derive_st_intervals([
            _row('2020-01-01', 'ST某股', 'ST'),
            _row('2020-06-01', '*ST某股', '从ST变为*ST'),
        ])
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]['start_date'], date(2020, 1, 1))
        self.assertIsNone(intervals[0]['end_date'])

    def test_non_st_row_closes_interval(self):
        intervals = derive_st_intervals([
            _row('2020-01-01', 'ST某股', 'ST'),
            _row('2021-01-01', '某股', '其他'),
        ])
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]['end_date'], date(2021, 1, 1))

    def test_two_separate_spells(self):
        intervals = derive_st_intervals([
            _row('2018-01-01', 'ST某股', 'ST'),
            _row('2019-01-01', '某股', '其他'),
            _row('2020-01-01', '*ST某股', '*ST'),
            _row('2021-01-01', '某股', '其他'),
        ])
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0]['end_date'], date(2019, 1, 1))
        self.assertEqual(intervals[1]['end_date'], date(2021, 1, 1))

    def test_trailing_st_open_ended(self):
        intervals = derive_st_intervals([_row('2020-01-01', 'ST某股', 'ST')])
        self.assertEqual(len(intervals), 1)
        self.assertIsNone(intervals[0]['end_date'])

    def test_delisting_consolidation_is_st(self):
        intervals = derive_st_intervals([_row('2023-05-09', '吉艾退', '退市整理期')])
        self.assertEqual(len(intervals), 1)

    def test_empty_records(self):
        self.assertEqual(derive_st_intervals([]), [])


class StResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.st_asset = Asset.objects.create(market=cls.market, symbol='600002', ts_code='600002.SH', name='ST某股')
        cls.clean_asset = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='某股')
        cls.delisted_asset = Asset.objects.create(
            market=cls.market, symbol='600003', ts_code='600003.SH', name='已退股',
            listing_status='D', delist_date=date(2021, 5, 1),
        )
        AssetNameChange.objects.create(asset=cls.st_asset, name='ST某股', start_date=date(2020, 1, 1), change_reason='ST', ann_date=date(2019, 12, 28))
        AssetNameChange.objects.create(asset=cls.st_asset, name='某股', start_date=date(2021, 1, 1), change_reason='其他', ann_date=date(2020, 12, 28))
        rebuild_asset_st_intervals(cls.st_asset.id)

    def test_as_of_boundaries(self):
        self.assertEqual(st_asset_ids_as_of(date(2019, 12, 31)), set())
        self.assertEqual(st_asset_ids_as_of(date(2020, 1, 1)), {self.st_asset.id})  # start inclusive
        self.assertEqual(st_asset_ids_as_of(date(2020, 12, 31)), {self.st_asset.id})
        self.assertEqual(st_asset_ids_as_of(date(2021, 1, 1)), set())  # end exclusive

    def test_batch_equals_single_day(self):
        dates = [date(2019, 12, 31), date(2020, 1, 1), date(2020, 6, 1), date(2021, 1, 1)]
        batch = st_asset_ids_by_dates(dates)
        for d in dates:
            self.assertEqual(batch[d], st_asset_ids_as_of(d), d)

    def test_delisted_non_st_asset_has_no_interval(self):
        # Survivorship discipline: delisting alone never creates an ST interval.
        self.assertEqual(AssetSTInterval.objects.filter(asset=self.delisted_asset).count(), 0)
        self.assertNotIn(self.delisted_asset.id, st_asset_ids_as_of(date(2020, 6, 1)))

    def test_rebuild_is_idempotent(self):
        first = rebuild_asset_st_intervals(self.st_asset.id)
        second = rebuild_asset_st_intervals(self.st_asset.id)
        self.assertEqual(first, second)
        self.assertEqual(AssetSTInterval.objects.filter(asset=self.st_asset).count(), 1)


class IngestGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.asset = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='某股')

    def test_lookahead_row_quarantined_not_stored(self):
        records = [{'ts_code': '600001.SH', 'name': 'ST某股', 'start_date': '20200101', 'ann_date': '20200105', 'change_reason': 'ST'}]
        stored, quarantined, touched = ingest_namechange_records(records, {'600001.SH': self.asset.id})
        self.assertEqual(stored, 0)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(touched, set())
        self.assertEqual(AssetNameChange.objects.count(), 0)

    def test_valid_row_stored(self):
        records = [{'ts_code': '600001.SH', 'name': 'ST某股', 'start_date': '20200101', 'ann_date': '20191228', 'change_reason': 'ST'}]
        stored, quarantined, touched = ingest_namechange_records(records, {'600001.SH': self.asset.id})
        self.assertEqual(stored, 1)
        self.assertEqual(quarantined, [])
        self.assertEqual(touched, {self.asset.id})
        self.assertEqual(AssetNameChange.objects.count(), 1)

    def test_unknown_ts_code_skipped(self):
        records = [{'ts_code': '999999.SH', 'name': 'ST某股', 'start_date': '20200101', 'ann_date': '20191228', 'change_reason': 'ST'}]
        stored, quarantined, touched = ingest_namechange_records(records, {'600001.SH': self.asset.id})
        self.assertEqual((stored, len(quarantined), touched), (0, 0, set()))

    def test_missing_start_date_quarantined(self):
        records = [{'ts_code': '600001.SH', 'name': 'ST某股', 'ann_date': '20191228', 'change_reason': 'ST'}]
        stored, quarantined, _ = ingest_namechange_records(records, {'600001.SH': self.asset.id})
        self.assertEqual(stored, 0)
        self.assertEqual(len(quarantined), 1)


class SyncFetchTests(SimpleTestCase):
    def test_full_page_bisects_window(self):
        from apps.markets.management.commands.sync_asset_namechanges import PAGE_LIMIT, _fetch
        calls = []

        def namechange(start_date, end_date):
            calls.append((start_date, end_date))
            if start_date == '20200101' and end_date == '20200131':
                return pd.DataFrame({'ts_code': ['600001.SH'] * PAGE_LIMIT})
            return pd.DataFrame({'ts_code': ['600001.SH'] * 3000})

        pro = type('Pro', (), {'namechange': staticmethod(namechange)})()
        records = _fetch(pro, date(2020, 1, 1), date(2020, 1, 31), 0.0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(records), 6000)

    def test_single_day_full_page_raises(self):
        from django.core.management.base import CommandError
        from apps.markets.management.commands.sync_asset_namechanges import PAGE_LIMIT, _fetch

        def namechange(start_date, end_date):
            return pd.DataFrame({'ts_code': ['600001.SH'] * PAGE_LIMIT})

        pro = type('Pro', (), {'namechange': staticmethod(namechange)})()
        with self.assertRaises(CommandError):
            _fetch(pro, date(2020, 1, 1), date(2020, 1, 1), 0.0)


class DailyRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.asset = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='某股')
        AssetSTInterval.objects.create(asset=cls.asset, start_date=date(2020, 1, 1), end_date=None)

    def test_divergence_between_open_interval_and_name_detected(self):
        from apps.markets.tasks import sync_recent_namechanges
        with override_settings(TUSHARE_TOKEN='test-token'), patch('apps.markets.tasks.ts.pro_api') as pro_api:
            pro_api.return_value.namechange.return_value = pd.DataFrame([])
            summary = sync_recent_namechanges(date(2020, 6, 1))
        self.assertEqual(summary['divergences'], 1)
        self.assertEqual(summary['rows_stored'], 0)

    def test_no_token_is_noop(self):
        from apps.markets.tasks import sync_recent_namechanges
        with override_settings(TUSHARE_TOKEN=''):
            summary = sync_recent_namechanges(date(2020, 6, 1))
        self.assertEqual(summary, {'rows_stored': 0, 'quarantined': 0, 'divergences': 0})