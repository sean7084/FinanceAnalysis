"""Tests for prediction gap detection and the reconciliation pass that heals it."""

import json
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.markets.models import Asset, ExchangeTradingCalendar, Market

from .models import ModelVersion, PredictionResult
from .models_lightgbm import LightGBMPrediction
from .reconciliation import (
    ENSEMBLE,
    LIGHTGBM,
    coverage_gaps,
    gaps_for_date,
    open_trading_dates,
    stored_asset_ids,
)
from .tasks import reconcile_prediction_coverage


class PredictionReconciliationTests(TestCase):
    """Gap detection over a calendar with two open days and one closed day."""

    EARLIER = date(2026, 4, 20)
    LATER = date(2026, 4, 21)
    CLOSED = date(2026, 4, 22)
    AS_OF = date(2026, 4, 22)

    def setUp(self):
        self.market = Market.objects.create(code='REC', name='Reconciliation Market')
        self.assets = [
            Asset.objects.create(
                market=self.market,
                symbol=f'60{index:04d}',
                ts_code=f'60{index:04d}.SH',
                name=f'Reconciliation Asset {index}',
            )
            for index in range(1, 4)
        ]
        self.version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='ensemble-reconciliation',
            status=ModelVersion.Status.READY,
            artifact_path='models/ensemble/reconciliation',
            is_active=True,
        )
        for trade_date, is_open in ((self.EARLIER, True), (self.LATER, True), (self.CLOSED, False)):
            ExchangeTradingCalendar.objects.create(
                exchange_code='SSE',
                trade_date=trade_date,
                is_open=is_open,
            )
        # coverage_gaps has to reach the universe through the real entry point, and
        # this database has no point-in-time membership for these fixture dates.
        patcher = patch(
            'apps.prediction.reconciliation.effective_universe_tradeable_assets',
            return_value=self.assets,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _store(self, asset, trade_date, source=ENSEMBLE, horizons=(3,)):
        if source == ENSEMBLE:
            PredictionResult.objects.create(
                asset=asset,
                date=trade_date,
                horizon_days=horizons[0],
                model_version=self.version,
                up_probability=Decimal('0.5'),
                flat_probability=Decimal('0.25'),
                down_probability=Decimal('0.25'),
                confidence=Decimal('0.5'),
                predicted_label='UP',
            )
        else:
            LightGBMPrediction.objects.create(
                asset=asset,
                date=trade_date,
                horizon_days=horizons[0],
                up_probability=Decimal('0.5'),
                flat_probability=Decimal('0.25'),
                down_probability=Decimal('0.25'),
                confidence=Decimal('0.5'),
                predicted_label='UP',
            )

    def test_open_trading_dates_returns_open_days_oldest_first(self):
        self.assertEqual(
            open_trading_dates(self.AS_OF, 5),
            [self.EARLIER, self.LATER],
        )

    def test_open_trading_dates_takes_the_most_recent_window(self):
        self.assertEqual(open_trading_dates(self.AS_OF, 1), [self.LATER])

    def test_open_trading_dates_with_no_lookback_is_empty(self):
        self.assertEqual(open_trading_dates(self.AS_OF, 0), [])

    def test_gaps_report_every_universe_member_as_missing_when_nothing_is_stored(self):
        gaps = gaps_for_date(self.LATER, universe=self.assets)
        self.assertEqual([gap.source for gap in gaps], [ENSEMBLE, LIGHTGBM])
        for gap in gaps:
            self.assertEqual(gap.universe_size, 3)
            self.assertEqual(gap.stored_asset_count, 0)
            self.assertEqual(gap.missing_asset_ids, tuple(asset.id for asset in self.assets))

    def test_one_row_for_an_asset_counts_as_that_asset_being_stored(self):
        """Horizon-shaped gaps are not treated as missing rows.

        LightGBM legitimately skips a horizon with no trained artifact, so a
        per-horizon expectation would report a repair job that is already done.
        """
        self._store(self.assets[0], self.LATER, horizons=(3,))
        gap = gaps_for_date(self.LATER, sources=(ENSEMBLE,), universe=self.assets)[0]
        self.assertEqual(gap.stored_asset_count, 1)
        self.assertEqual(gap.missing_asset_ids, tuple(asset.id for asset in self.assets[1:]))

    def test_rows_for_non_universe_assets_do_not_count_as_coverage(self):
        outsider = Asset.objects.create(
            market=self.market, symbol='000999', ts_code='000999.SZ', name='Outsider',
        )
        self._store(outsider, self.LATER)
        self.assertEqual(stored_asset_ids(ENSEMBLE, self.LATER), {outsider.id})
        gap = gaps_for_date(self.LATER, sources=(ENSEMBLE,), universe=self.assets)[0]
        self.assertEqual(gap.stored_asset_count, 0)

    def test_coverage_gaps_walks_dates_oldest_first(self):
        gaps = coverage_gaps(as_of=self.AS_OF, lookback_trading_days=5)
        self.assertEqual([gap.trade_date for gap in gaps], [self.EARLIER, self.EARLIER, self.LATER, self.LATER])

    def test_coverage_gaps_accepts_an_as_of_string(self):
        self.assertEqual(len(coverage_gaps(as_of=self.AS_OF.isoformat(), lookback_trading_days=1)), 2)


class ReconcilePredictionCoverageTaskTests(TestCase):
    """The repair pass: oldest hole first, bounded per run, resumable."""

    def setUp(self):
        self.market = Market.objects.create(code='RECT', name='Reconcile Task Market')
        self.assets = [
            Asset.objects.create(
                market=self.market,
                symbol=f'61{index:04d}',
                ts_code=f'61{index:04d}.SH',
                name=f'Reconcile Asset {index}',
            )
            for index in range(3)
        ]
        self.earlier = date(2026, 5, 11)
        self.later = date(2026, 5, 12)
        for trade_date in (self.earlier, self.later):
            ExchangeTradingCalendar.objects.create(exchange_code='SSE', trade_date=trade_date, is_open=True)

        universe_patch = patch(
            'apps.prediction.tasks.effective_universe_tradeable_assets',
            side_effect=lambda trade_date, context=None, **kwargs: list(self.assets),
        )
        self.universe = universe_patch.start()
        self.addCleanup(universe_patch.stop)

        prewarm_patch = patch('apps.analytics.tasks.prewarm_dashboard_stocks_cache')
        self.prewarm = prewarm_patch.start()
        self.addCleanup(prewarm_patch.stop)

    def _capture_ensemble_writes(self):
        """Pretend to generate, but write a row so a second run sees progress."""
        version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='ensemble-reconciled',
            status=ModelVersion.Status.READY,
            artifact_path='models/ensemble/reconciled',
            is_active=True,
        )

        def fake_single_asset(asset, as_of, horizons, macro_phase=None, event_tag=None):
            PredictionResult.objects.create(
                asset=asset,
                date=as_of,
                horizon_days=horizons[0],
                model_version=version,
                up_probability=Decimal('0.5'),
                flat_probability=Decimal('0.25'),
                down_probability=Decimal('0.25'),
                confidence=Decimal('0.5'),
                predicted_label='UP',
            )
            return 1

        patcher = patch(
            'apps.prediction.tasks._generate_predictions_for_single_asset',
            side_effect=fake_single_asset,
        )
        fake = patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_repairs_the_oldest_incomplete_date_first(self):
        fake = self._capture_ensemble_writes()
        result = reconcile_prediction_coverage(
            target_date=self.later.isoformat(),
            sources=(ENSEMBLE,),
            max_assets=1,
        )
        self.assertEqual(fake.call_args.kwargs['as_of'], self.earlier)
        self.assertIn('2026-05-11', result)

    def test_caps_the_batch_and_reports_what_is_left(self):
        fake = self._capture_ensemble_writes()
        result = reconcile_prediction_coverage(
            target_date=self.later.isoformat(),
            sources=(ENSEMBLE,),
            max_assets=2,
        )
        self.assertEqual(fake.call_count, 2)
        self.assertIn('1 assets still missing', result)
        self.assertIn('next run continues', result)

    def test_repeated_runs_close_the_date_and_move_on(self):
        self._capture_ensemble_writes()
        for _ in range(2):
            reconcile_prediction_coverage(target_date=self.later.isoformat(), sources=(ENSEMBLE,), max_assets=2)
        self.assertEqual(PredictionResult.objects.filter(date=self.earlier).count(), 3)
        result = reconcile_prediction_coverage(
            target_date=self.later.isoformat(),
            sources=(ENSEMBLE,),
            max_assets=2,
        )
        self.assertIn('2026-05-12', result)

    def test_reports_complete_when_nothing_is_missing(self):
        version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='ensemble-already-there',
            status=ModelVersion.Status.READY,
            artifact_path='models/ensemble/already-there',
            is_active=True,
        )
        for trade_date in (self.earlier, self.later):
            for asset in self.assets:
                PredictionResult.objects.create(
                    asset=asset,
                    date=trade_date,
                    horizon_days=3,
                    model_version=version,
                    up_probability=Decimal('0.5'),
                    flat_probability=Decimal('0.25'),
                    down_probability=Decimal('0.25'),
                    confidence=Decimal('0.5'),
                    predicted_label='UP',
                )
        result = reconcile_prediction_coverage(target_date=self.later.isoformat(), sources=(ENSEMBLE,))
        self.assertIn('complete through', result)
        self.prewarm.delay.assert_not_called()

    def test_zero_max_assets_reports_the_gap_without_writing(self):
        fake = self._capture_ensemble_writes()
        result = reconcile_prediction_coverage(
            target_date=self.later.isoformat(),
            sources=(ENSEMBLE,),
            max_assets=0,
        )
        fake.assert_not_called()
        self.assertIn('disabled by max_assets=0', result)
        self.assertIn('missing', result)

    def test_a_repaired_batch_rewarms_the_dashboard_cache(self):
        """New rows for the same factor date do not invalidate the cached payload."""
        self._capture_ensemble_writes()
        reconcile_prediction_coverage(target_date=self.later.isoformat(), sources=(ENSEMBLE,), max_assets=1)
        self.prewarm.delay.assert_called_once_with()


class CheckPredictionCoverageCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='RECC', name='Reconcile Command Market')
        self.asset = Asset.objects.create(
            market=self.market, symbol='620001', ts_code='620001.SH', name='Command Asset',
        )
        self.trade_date = date(2026, 6, 10)
        ExchangeTradingCalendar.objects.create(
            exchange_code='SSE', trade_date=self.trade_date, is_open=True,
        )
        self.patcher = patch(
            'apps.prediction.reconciliation.effective_universe_tradeable_assets',
            return_value=[self.asset],
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='ensemble-command',
            status=ModelVersion.Status.READY,
            artifact_path='models/ensemble/command',
            is_active=True,
        )

    def _run(self, **options):
        out, err = StringIO(), StringIO()
        code = call_command('check_prediction_coverage', stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue(), code

    def test_reports_a_missing_universe_member_for_both_sources(self):
        stdout, _, _ = self._run(as_of=self.trade_date.isoformat(), lookback_trading_days=1)
        self.assertIn('ensemble: 0/1 assets stored, 1 missing', stdout)
        self.assertIn('lightgbm: 0/1 assets stored, 1 missing', stdout)

    def test_json_output_is_parseable(self):
        stdout, _, _ = self._run(as_of=self.trade_date.isoformat(), lookback_trading_days=1, json=True)
        payload = json.loads(stdout)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]['universe_size'], 1)
        self.assertEqual(payload[0]['missing_assets'], 1)

    def test_fail_on_gaps_exits_non_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(as_of=self.trade_date.isoformat(), lookback_trading_days=1, fail_on_gaps=True)
        self.assertEqual(ctx.exception.code, 1)

    def test_a_complete_date_is_reported_and_does_not_fail_the_gate(self):
        common = {
            'asset': self.asset,
            'date': self.trade_date,
            'horizon_days': 3,
            'up_probability': Decimal('0.5'),
            'flat_probability': Decimal('0.25'),
            'down_probability': Decimal('0.25'),
            'confidence': Decimal('0.5'),
            'predicted_label': 'UP',
        }
        PredictionResult.objects.create(model_version=self.version, **common)
        LightGBMPrediction.objects.create(**common)
        stdout, _, _ = self._run(
            as_of=self.trade_date.isoformat(), lookback_trading_days=1, fail_on_gaps=True,
        )
        self.assertIn('ensemble: 1/1 assets stored, 0 missing', stdout)
        self.assertIn('lightgbm: 1/1 assets stored, 0 missing', stdout)

    def test_bad_as_of_is_a_command_error(self):
        with self.assertRaises(CommandError):
            self._run(as_of='not-a-date', lookback_trading_days=1)
