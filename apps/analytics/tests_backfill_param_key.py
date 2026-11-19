"""The backfill's SQL canonicalisation must equal the Python helper, key for key.

This is the plan's main correctness risk: the bulk path builds keys in SQL to avoid
transferring ~173M jsonb payloads over the NAS link, and a formatting divergence between
``->>`` and ``str()`` would write keys the readers do not recognise. Rows are created with
``bulk_create`` so they start with a NULL key, exactly like the pre-backfill production
state -- ``save()`` would otherwise fill them and hide the defect.
"""
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.analytics.indicator_params import canonical_param_key
from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import session_key_timestamp
from apps.markets.models import Asset, Market


class BackfillIndicatorParamKeyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='CN', name='China')
        cls.asset = Asset.objects.create(
            market=cls.market, symbol='600100', ts_code='600100.SH', name='Backfill',
            list_date=date(2015, 1, 1),
        )
        cls.stamp = session_key_timestamp(date(2026, 11, 6))
        cls.payloads = [
            ('SMA', {'timeperiod': 50}),
            ('SMA', {'timeperiod': 200}),
            ('EMA', {'timeperiod': 12}),
            ('OBV', {}),
            ('RS_SCORE', {}),
            ('MACD', {'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9}),
            ('STOCH', {'fastk_period': 14, 'slowk_period': 3, 'slowd_period': 3}),
            ('BBANDS', {'timeperiod': 20, 'nbdevup': 2, 'nbdevdn': 2,
                        'upper': 5.06461702, 'middle': 4.743085, 'lower': 4.42155298}),
            ('REALIZED_VOLATILITY_5D', {'window': 5}),
        ]
        # bulk_create bypasses save(), so param_key stays NULL -- the pre-backfill state.
        TechnicalIndicator.objects.bulk_create([
            TechnicalIndicator(
                asset=cls.asset, timestamp=cls.stamp, indicator_type=kind,
                value=Decimal('1.00000000'), parameters=params,
            )
            for kind, params in cls.payloads
        ])

    def _run(self, *extra):
        call_command(
            'backfill_indicator_param_key', '--asset-id', str(self.asset.pk),
            '--start-year', '2026', '--end-year', '2026', *extra,
            stdout=self.stdout_buffer, stderr=self.stderr_buffer,
        )

    def setUp(self):
        from io import StringIO
        self.stdout_buffer = StringIO()
        self.stderr_buffer = StringIO()

    def test_rows_start_with_null_keys(self):
        self.assertEqual(
            TechnicalIndicator.objects.filter(param_key__isnull=True).count(), len(self.payloads)
        )

    def test_dry_run_writes_nothing(self):
        self._run()
        self.assertEqual(
            TechnicalIndicator.objects.filter(param_key__isnull=True).count(), len(self.payloads)
        )
        self.assertIn('execute=False', self.stdout_buffer.getvalue())

    def test_execute_matches_python_helper_for_every_row(self):
        self._run('--execute')
        rows = TechnicalIndicator.objects.all()
        self.assertEqual(rows.count(), len(self.payloads))
        for row in rows:
            with self.subTest(indicator_type=row.indicator_type, parameters=row.parameters):
                self.assertEqual(
                    row.param_key, canonical_param_key(row.indicator_type, row.parameters)
                )
        self.assertEqual(TechnicalIndicator.objects.filter(param_key__isnull=True).count(), 0)

    def test_bbands_key_excludes_computed_outputs(self):
        self._run('--execute')
        bbands = TechnicalIndicator.objects.get(indicator_type='BBANDS')
        self.assertEqual(bbands.param_key, 'nbdevdn=2,nbdevup=2,timeperiod=20')
        # The payload itself is untouched -- consumers read the bands from it.
        self.assertIn('upper', bbands.parameters)
        self.assertIn('lower', bbands.parameters)

    def test_empty_parameter_sets_become_empty_string_not_null(self):
        self._run('--execute')
        for kind in ('OBV', 'RS_SCORE'):
            self.assertEqual(TechnicalIndicator.objects.get(indicator_type=kind).param_key, '')

    def test_second_run_is_a_noop(self):
        self._run('--execute')
        before = set(TechnicalIndicator.objects.values_list('pk', 'param_key'))
        self._run('--execute')
        self.assertEqual(set(TechnicalIndicator.objects.values_list('pk', 'param_key')), before)
        self.assertIn('updated=0', self.stdout_buffer.getvalue())

    def test_scoped_run_succeeds_even_though_other_rows_remain_null(self):
        """A bounded run must not fail just because it was bounded.

        The completeness check is only meaningful for an unscoped run; otherwise the safe
        incremental path (one asset, one year) reports failure every time.
        """
        other = Asset.objects.create(
            market=self.market, symbol='600101', ts_code='600101.SH', name='Other',
            list_date=date(2015, 1, 1),
        )
        TechnicalIndicator.objects.bulk_create([
            TechnicalIndicator(
                asset=other, timestamp=self.stamp, indicator_type='RSI',
                value=Decimal('2.00000000'), parameters={'timeperiod': 14},
            )
        ])
        self._run('--execute')
        self.assertIsNone(TechnicalIndicator.objects.get(asset=other).param_key)
        self.assertEqual(
            TechnicalIndicator.objects.filter(asset=self.asset, param_key__isnull=True).count(), 0
        )
        self.assertIn('remaining_null_in_scope=0', self.stdout_buffer.getvalue())

    def test_after_asset_id_skips_earlier_assets(self):
        """Resume watermark: earlier assets must be left untouched, later ones filled."""
        other = Asset.objects.create(
            market=self.market, symbol='600101', ts_code='600101.SH', name='Later',
            list_date=date(2015, 1, 1),
        )
        TechnicalIndicator.objects.bulk_create([
            TechnicalIndicator(
                asset=other, timestamp=self.stamp, indicator_type='RSI',
                value=Decimal('2.00000000'), parameters={'timeperiod': 14},
            )
        ])
        self.assertGreater(other.pk, self.asset.pk)
        # Call directly: the _run helper always passes --asset-id, which would override
        # the watermark and leave an empty asset list.
        call_command(
            'backfill_indicator_param_key', '--execute',
            '--after-asset-id', str(self.asset.pk),
            '--start-year', '2026', '--end-year', '2026',
            stdout=self.stdout_buffer, stderr=self.stderr_buffer,
        )
        self.assertEqual(
            TechnicalIndicator.objects.filter(asset=self.asset, param_key__isnull=True).count(),
            len(self.payloads),
        )
        self.assertEqual(
            TechnicalIndicator.objects.get(asset=other).param_key, 'timeperiod=14'
        )

    def test_by_id_sweep_populates_every_key(self):
        """Id-range chunking is a measured dead end (3,725 rows/s vs 8,828) but must still
        be correct, so it stays tested rather than becoming untested surface."""
        self._run('--execute', '--by-id', '--id-chunk', '2000')
        rows = TechnicalIndicator.objects.all()
        self.assertEqual(rows.count(), len(self.payloads))
        for row in rows:
            self.assertEqual(
                row.param_key, canonical_param_key(row.indicator_type, row.parameters)
            )
        self.assertIn('passes=', self.stdout_buffer.getvalue())

    def test_by_ctid_sweep_populates_every_key_and_converges(self):
        """The physical-order path must fill every row and terminate.

        Block ranges replaced asset-year chunking after the latter measured 79%
        IO/DataFileRead; the sweep repeats passes until one updates nothing, because a
        non-HOT update can move a row behind the cursor.
        """
        self._run('--execute', '--by-ctid', '--blocks-per-chunk', '1')
        rows = TechnicalIndicator.objects.all()
        self.assertEqual(rows.count(), len(self.payloads))
        for row in rows:
            self.assertEqual(
                row.param_key, canonical_param_key(row.indicator_type, row.parameters)
            )
        out = self.stdout_buffer.getvalue()
        self.assertIn('updated=', out)
        self.assertIn('passes=', out)
        self.assertEqual(TechnicalIndicator.objects.filter(param_key__isnull=True).count(), 0)

    def test_by_ctid_dry_run_writes_nothing(self):
        self._run('--by-ctid')
        self.assertEqual(
            TechnicalIndicator.objects.filter(param_key__isnull=True).count(), len(self.payloads)
        )

    def test_verify_sample_aborts_when_sql_and_python_diverge(self):
        """The cross-check must be able to fail, not just report a count."""
        from apps.analytics.management.commands import backfill_indicator_param_key as module

        original = module.canonical_param_key
        module.canonical_param_key = lambda kind, params: 'DELIBERATELY_WRONG'
        try:
            with self.assertRaises(CommandError) as caught:
                self._run('--execute')
            self.assertIn('differ between SQL and Python', str(caught.exception))
        finally:
            module.canonical_param_key = original
        self.assertEqual(
            TechnicalIndicator.objects.filter(param_key__isnull=True).count(), len(self.payloads),
            'the abort must happen before any write',
        )