"""STK-04: the stock date contract is enforced on backtests (issue #192).

The serializer guards API/UI submissions; the task entry guards command-driven
runs, which create BacktestRun rows directly and bypass the serializer. Both
accept the research-only override, and the override is recorded in parameters.
"""
from datetime import date
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.backtest.models import BacktestRun
from apps.backtest.serializers import BacktestRunSerializer
from apps.backtest.tasks import run_backtest
from apps.core.date_contract import STOCK_BACKTEST_START


def _params(**overrides):
    base = {
        'top_n': 3,
        'horizon_days': 7,
        'up_threshold': 0.5,
        'prediction_source': 'heuristic',
    }
    base.update(overrides)
    return base


def _payload(start, **overrides):
    payload = {
        'name': 'contract-test',
        'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
        'start_date': start,
        'end_date': date(2018, 6, 29),
        'parameters': _params(),
    }
    payload.update(overrides)
    return payload


class BacktestSerializerContractTests(TestCase):
    def test_pre_contract_start_rejected(self):
        serializer = BacktestRunSerializer(data=_payload(date(2017, 6, 1)))
        self.assertFalse(serializer.is_valid())
        self.assertIn('start_date', serializer.errors)
        self.assertIn('2018-01-02', str(serializer.errors['start_date']))

    def test_boundary_start_accepted(self):
        serializer = BacktestRunSerializer(data=_payload(STOCK_BACKTEST_START))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_override_accepted_and_recorded(self):
        serializer = BacktestRunSerializer(
            data=_payload(date(2017, 6, 1), parameters=_params(allow_pre_contract_history=True))
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.validated_data['parameters']['allow_pre_contract_history'], True)

    def test_override_must_be_boolean(self):
        serializer = BacktestRunSerializer(
            data=_payload(date(2017, 6, 1), parameters=_params(allow_pre_contract_history='yes'))
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('parameters', serializer.errors)


class BacktestTaskContractTests(TestCase):
    def _run(self, start, parameters):
        return BacktestRun.objects.create(
            name='contract-task-test',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=start,
            end_date=date(2018, 6, 29),
            status=BacktestRun.Status.PENDING,
            parameters=parameters,
        )

    def test_task_entry_refuses_pre_contract_run(self):
        run = self._run(date(2017, 6, 1), _params())
        result = run_backtest(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, BacktestRun.Status.FAILED)
        self.assertIn('2018-01-02', run.error_message)
        self.assertIn('refused by the date contract', result)
        self.assertEqual(run.trades.count(), 0)

    def test_task_entry_override_passes_guard(self):
        run = self._run(date(2017, 6, 1), _params(allow_pre_contract_history=True))
        with patch('apps.backtest.tasks._get_trading_dates', side_effect=RuntimeError('PAST_GUARD')):
            run_backtest(run.id)
        run.refresh_from_db()
        self.assertIn('PAST_GUARD', run.error_message)

    def test_task_entry_boundary_passes_guard(self):
        run = self._run(STOCK_BACKTEST_START, _params())
        with patch('apps.backtest.tasks._get_trading_dates', side_effect=RuntimeError('PAST_GUARD')):
            run_backtest(run.id)
        run.refresh_from_db()
        self.assertIn('PAST_GUARD', run.error_message)


class ValidationCommandContractTests(TestCase):
    def test_command_rejects_pre_contract_window(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'run_validation_backtests',
                '--start-date', '2017-01-03', '--end-date', '2017-06-30',
                '--sources', 'heuristic',
            )
        self.assertIn('2018-01-02', str(ctx.exception))
        self.assertEqual(BacktestRun.objects.count(), 0)

    def test_command_override_records_flag(self):
        with patch('apps.backtest.management.commands.run_validation_backtests.run_backtest'):
            call_command(
                'run_validation_backtests',
                '--start-date', '2017-01-03', '--end-date', '2017-03-31',
                '--sources', 'heuristic', '--allow-pre-contract-history',
            )
        runs = BacktestRun.objects.all()
        self.assertGreater(runs.count(), 0)
        for run in runs:
            self.assertIs(run.parameters['allow_pre_contract_history'], True)