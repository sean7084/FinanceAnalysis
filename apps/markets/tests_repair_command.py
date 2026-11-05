"""Tests for the OHLCV adjustment-basis repair command.

The selection logic is what the batch's correctness rests on, so it is tested
directly rather than only through ``handle()``. The invariant worth protecting is
that each asset is repaired from its own earliest stored date: starting at
``HISTORICAL_DATA_FLOOR`` instead would strand pre-floor rows without a
``raw_close`` forever, and the all-or-nothing re-base rule would then refuse to
re-base that asset on every future ex-dividend event.
"""

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from apps.markets.models import Asset, Market, OHLCV
from apps.markets.management.commands.repair_ohlcv_adjustment_basis import (
    Command,
)


def make_asset(market, symbol, ts_code, tagged, first_date):
    asset = Asset.objects.create(
        market=market, symbol=symbol, ts_code=ts_code,
        name=f'{symbol} Corp', membership_tags=['CSI500'] if tagged else [],
    )
    OHLCV.objects.create(
        asset=asset, date=first_date, open=Decimal('10'), high=Decimal('10'),
        low=Decimal('10'), close=Decimal('10'), adj_close=Decimal('10'),
        volume=100, amount=Decimal('1000'),
    )
    OHLCV.objects.create(
        asset=asset, date=date(first_date.year, first_date.month + 1 if first_date.month < 12 else 1, 3),
        open=Decimal('11'), high=Decimal('11'), low=Decimal('11'),
        close=Decimal('11'), adj_close=Decimal('11'),
        volume=100, amount=Decimal('1000'), raw_close=Decimal('11'),
        adj_factor=Decimal('1.000000'),
    )
    return asset


class RepairAssetSelectionTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.tagged = make_asset(self.market, '600001', '600001.SH', True, date(2008, 7, 2))
        self.untagged = make_asset(self.market, '600002', '600002.SH', False, date(2012, 3, 1))
        self.command = Command()

    def options(self, **overrides):
        base = {'symbols': '', 'all_assets': False, 'limit': 0}
        base.update(overrides)
        return base

    def select(self, **overrides):
        return self.command._select_assets(self.options(**overrides))

    def test_default_selection_is_the_synced_universe(self):
        selected = self.select()
        self.assertEqual([entry['asset'].pk for entry in selected], [self.tagged.pk])

    def test_all_assets_includes_themes_outside_the_sync_scope(self):
        selected = self.select(all_assets=True)
        self.assertEqual(
            sorted(entry['asset'].pk for entry in selected),
            sorted([self.tagged.pk, self.untagged.pk]),
        )

    def test_explicit_symbols_override_the_universe_filter(self):
        selected = self.select(symbols='600002.SH')
        self.assertEqual([entry['asset'].pk for entry in selected], [self.untagged.pk])

    def test_first_date_is_the_asset_own_earliest_row_not_the_floor(self):
        # The point of the whole selection: a 2008 listing must be repaired from 2008.
        entry = self.select()[0]
        self.assertEqual(entry['first_date'], date(2008, 7, 2))

    def test_legacy_count_excludes_rows_that_already_carry_raw_close(self):
        entry = self.select()[0]
        self.assertEqual(entry['legacy_rows'], 1)

    def test_assets_without_ohlcv_rows_are_not_selected(self):
        Asset.objects.create(market=self.market, symbol='600009', ts_code='600009.SH',
                             name='No Rows', membership_tags=['CSI500'])
        selected = self.select()
        self.assertEqual(len(selected), 1)

    def test_limit_caps_the_queue(self):
        self.assertEqual(len(self.select(all_assets=True, limit=1)), 1)


class RepairExecutionTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.asset = make_asset(self.market, '600001', '600001.SH', True, date(2024, 1, 2))

    def _stub_provider(self):
        class StubPro:
            def daily(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240102', 'open': 10.0, 'high': 10.0, 'low': 10.0,
                     'close': 10.0, 'vol': 1.0, 'amount': 10.0},
                    {'trade_date': '20240103', 'open': 11.0, 'high': 11.0, 'low': 11.0,
                     'close': 11.0, 'vol': 1.0, 'amount': 10.0},
                ])

            def adj_factor(self, **kwargs):
                return pd.DataFrame([
                    {'trade_date': '20240102', 'adj_factor': 1.0},
                    {'trade_date': '20240103', 'adj_factor': 1.0},
                ])

        return StubPro()

    def test_second_run_skips_a_repaired_asset_without_calling_the_provider(self):
        # What makes an interrupted batch cheap to resume: finished assets are not
        # re-fetched, so a re-run spends calls only on what is still missing.
        from django.core.management.base import CommandError

        with patch('apps.markets.tasks.ts.pro_api', return_value=self._stub_provider()), \
                patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            call_command('repair_ohlcv_adjustment_basis', stdout=StringIO())
            repaired = list(OHLCV.objects.order_by('date').values_list('date', 'close', 'raw_close'))

        self.assertTrue(all(row[2] is not None for row in repaired))
        self.assertEqual(len(repaired), 3)

        with patch('apps.markets.tasks.ts.pro_api', side_effect=AssertionError('provider called')):
            with self.assertRaises(CommandError):
                call_command('repair_ohlcv_adjustment_basis', stdout=StringIO())

        # Unchanged, and no provider call was made.
        self.assertEqual(
            repaired,
            list(OHLCV.objects.order_by('date').values_list('date', 'close', 'raw_close')),
        )

    def test_every_row_ends_up_with_a_basis_and_a_factor(self):
        with patch('apps.markets.tasks.ts.pro_api', return_value=self._stub_provider()), \
                patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            call_command('repair_ohlcv_adjustment_basis', stdout=StringIO())

        self.assertEqual(OHLCV.objects.filter(raw_close__isnull=True).count(), 0)
        for row in OHLCV.objects.all():
            self.assertIsNotNone(row.adj_factor)

    def test_dry_run_reports_without_calling_the_provider(self):
        with patch('apps.markets.tasks.ts.pro_api', side_effect=AssertionError('provider called')):
            out = StringIO()
            call_command('repair_ohlcv_adjustment_basis', '--dry-run', stdout=out)

        self.assertIn('1 asset(s), 1 row(s) without raw_close', out.getvalue())
        self.assertIn('600001.SH', out.getvalue())
        self.assertEqual(OHLCV.objects.filter(raw_close__isnull=True).count(), 1)

    def test_no_matching_asset_is_an_error_not_a_silent_success(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('repair_ohlcv_adjustment_basis', '--symbols', '999999.SZ',
                         stdout=StringIO())

