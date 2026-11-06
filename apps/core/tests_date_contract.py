"""Tests for the per-asset-class date contract.

Three things are pinned here, in descending order of how likely they are to drift:

* the boundary values themselves, because moving one re-baselines every headline
  metric and must never happen as a side effect of an unrelated change;
* the enforcement rule, because a refused ``--start`` is the only thing standing
  between a research window and a reported production number;
* the arithmetic linking a training boundary to its backtest boundary, because
  ``2021-01-13`` is not a round number -- it is the 251st eligible session, and
  a reader who does not know that will "tidy" it back to a month start.
"""

from datetime import date, timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.core.date_contract import (
    ASSET_ETF,
    ASSET_STOCKS,
    ETF_BACKTEST_START,
    ETF_RAW_DATA_FROM,
    ETF_TRAINING_START,
    PRE_CONTRACT_FLAG,
    STOCK_BACKTEST_START,
    STOCK_RAW_DATA_FROM,
    STOCK_TRAINING_START,
    TRAINING_FLOORS,
    contract_floor,
    enforce_date_contract,
)
from apps.etf_rotation.walk_forward import expanding_folds

WALK_FORWARD_MIN_TRAIN_SESSIONS = 250


def weekday_sessions(start, count):
    """``count`` consecutive Mon-Fri dates beginning at ``start``.

    A stand-in for a trading calendar that needs no database. Weekends are skipped
    so the indices line up the way the engine sees them.
    """
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


class ContractBoundaryTests(SimpleTestCase):
    """The values are the decision. If one changes, this test fails first."""

    def test_boundaries_are_the_adopted_dates(self):
        self.assertEqual(STOCK_RAW_DATA_FROM, date(2008, 7, 2))
        self.assertEqual(STOCK_TRAINING_START, date(2016, 6, 1))
        self.assertEqual(STOCK_BACKTEST_START, date(2018, 1, 2))
        self.assertEqual(ETF_RAW_DATA_FROM, date(2016, 1, 4))
        self.assertEqual(ETF_TRAINING_START, date(2020, 1, 2))
        self.assertEqual(ETF_BACKTEST_START, date(2021, 1, 13))

    def test_evaluation_never_precedes_training(self):
        self.assertLess(STOCK_TRAINING_START, STOCK_BACKTEST_START)
        self.assertLess(ETF_TRAINING_START, ETF_BACKTEST_START)

    def test_retention_precedes_training_for_both_classes(self):
        self.assertLess(STOCK_RAW_DATA_FROM, STOCK_TRAINING_START)
        self.assertLess(ETF_RAW_DATA_FROM, ETF_TRAINING_START)

    def test_lookup_tables_agree_with_constants(self):
        self.assertEqual(contract_floor(ASSET_STOCKS, 'training'), STOCK_TRAINING_START)
        self.assertEqual(contract_floor(ASSET_ETF, 'backtest'), ETF_BACKTEST_START)
        self.assertEqual(TRAINING_FLOORS[ASSET_ETF], ETF_TRAINING_START)

    def test_unknown_key_is_refused_rather_than_defaulted(self):
        with self.assertRaises(CommandError):
            contract_floor('crypto', 'training')


class EnforceDateContractTests(SimpleTestCase):

    def test_absent_start_resolves_to_the_boundary(self):
        self.assertEqual(
            enforce_date_contract(asset_class=ASSET_ETF, stage='training', requested_start=None),
            ETF_TRAINING_START,
        )

    def test_boundary_itself_is_allowed(self):
        self.assertEqual(
            enforce_date_contract(
                asset_class=ASSET_ETF, stage='backtest', requested_start=ETF_BACKTEST_START,
            ),
            ETF_BACKTEST_START,
        )

    def test_pre_contract_start_is_refused_with_the_boundary_named(self):
        with self.assertRaisesMessage(
            CommandError,
            f'precedes the {ASSET_ETF} date contract boundary {ETF_BACKTEST_START.isoformat()}',
        ):
            enforce_date_contract(
                asset_class=ASSET_ETF, stage='backtest',
                requested_start=ETF_BACKTEST_START - timedelta(days=1),
            )

    def test_escape_hatch_admits_the_research_window(self):
        early = ETF_BACKTEST_START - timedelta(days=365)
        self.assertEqual(
            enforce_date_contract(
                asset_class=ASSET_ETF, stage='backtest',
                requested_start=early, allow_pre_contract=True,
            ),
            early,
        )

    def test_refusal_advertises_the_flag_it_asks_for(self):
        with self.assertRaisesMessage(CommandError, f'--{PRE_CONTRACT_FLAG}'):
            enforce_date_contract(
                asset_class=ASSET_STOCKS, stage='training', requested_start=date(2012, 1, 4),
            )


class BacktestStartArithmeticTests(SimpleTestCase):
    """``expanding_folds`` reserves ``min_train_days`` sessions, so the first
    validated date is the session *after* them -- not the 250th.

    These run against a synthetic Mon-Fri calendar because the Django test database
    is created empty, so the real panel cannot be measured here. The live figures
    below were measured against the populated development database and are what
    ``ETF_BACKTEST_START`` was derived from; if the eligible-session calendar is ever
    rebuilt materially differently, re-derive the boundary rather than trusting it.

        2020-01-02 .. 2021-01-04  ->  244 distinct eligible sessions (243 half-open)
        250th eligible session    ->  2021-01-12
        251st eligible session    ->  2021-01-13  == ETF_BACKTEST_START
        2016-06-01 .. 2018-01-02  ->  388 sessions (~19 months)

    Confirmed end to end by the post-contract ETF retrain: fold 0 trained
    ``2020-01-02..2021-01-12`` and validated from ``2021-01-13``.

    Counting trap: ``.values('date').distinct()`` does NOT deduplicate when the model
    declares ``Meta.ordering`` -- the ordering column joins the ``DISTINCT`` and every
    row survives. It reported "250th session: 2020-01-09" here. Use ``.order_by()``
    before ``values_list``, or ``Count(distinct=True)``.
    """

    def test_first_validation_date_is_one_past_the_training_reserve(self):
        sessions = weekday_sessions(ETF_TRAINING_START, WALK_FORWARD_MIN_TRAIN_SESSIONS + 40)
        folds = expanding_folds(
            sessions[0], sessions[-1], n_folds=4,
            min_train_days=WALK_FORWARD_MIN_TRAIN_SESSIONS, trade_dates=sessions,
        )
        self.assertTrue(folds, 'a panel with the reserve plus validation room must yield folds')
        self.assertEqual(folds[0].valid_start, sessions[WALK_FORWARD_MIN_TRAIN_SESSIONS])
        self.assertEqual(folds[0].train_end, sessions[WALK_FORWARD_MIN_TRAIN_SESSIONS - 1])

    def test_a_window_shorter_than_the_reserve_yields_nothing(self):
        sessions = weekday_sessions(ETF_TRAINING_START, WALK_FORWARD_MIN_TRAIN_SESSIONS - 1)
        self.assertEqual(
            expanding_folds(
                sessions[0], sessions[-1], n_folds=4,
                min_train_days=WALK_FORWARD_MIN_TRAIN_SESSIONS, trade_dates=sessions,
            ),
            [],
        )


class CommandEnforcementTests(SimpleTestCase):
    """The raise and the default must move together: a command whose default is
    behind its own guard can never be run without a flag.

    Only the guard is exercised. Past it the commands backfill and train, which is
    not what these tests are about and would not finish inside a test run.
    """

    @staticmethod
    def _parser(command_class):
        from django.core.management.base import CommandParser

        parser = CommandParser(prog=command_class.__name__)
        command_class().add_arguments(parser)
        return parser

    def test_rebuild_refuses_a_pre_contract_start_date(self):
        from apps.prediction.management.commands.rebuild_lightgbm_pipeline import Command

        with self.assertRaisesMessage(
            CommandError,
            f'precedes the {ASSET_STOCKS} date contract boundary {STOCK_TRAINING_START.isoformat()}',
        ):
            call_command('rebuild_lightgbm_pipeline', start_date='2015-01-01')

    def test_lstm_rebuild_refuses_a_pre_contract_start_date(self):
        with self.assertRaisesMessage(
            CommandError,
            f'precedes the {ASSET_STOCKS} date contract boundary {STOCK_TRAINING_START.isoformat()}',
        ):
            call_command('rebuild_lstm_pipeline', start_date='2015-01-01')

    def test_backtest_refuses_a_pre_contract_start_date(self):
        with self.assertRaisesMessage(
            CommandError,
            f'precedes the {ASSET_ETF} date contract boundary {ETF_BACKTEST_START.isoformat()}',
        ):
            call_command('run_etf_rotation_backtest', start='2016-01-29', end='2023-12-31')

    def test_escape_hatch_flag_is_registered_where_the_raise_is_enforced(self):
        from apps.etf_rotation.management.commands.run_etf_rotation_backtest import (
            Command as BacktestCommand,
        )
        from apps.etf_rotation.management.commands.sweep_etf_rotation_lstm import (
            Command as SweepCommand,
        )
        from apps.etf_rotation.management.commands.train_etf_rotation_model import (
            Command as TrainCommand,
        )
        from apps.prediction.management.commands.rebuild_lightgbm_pipeline import (
            Command as LightGBMCommand,
        )
        from apps.prediction.management.commands.rebuild_lstm_pipeline import (
            Command as LSTMCommand,
        )

        for command_class in (
            BacktestCommand, SweepCommand, TrainCommand, LightGBMCommand, LSTMCommand,
        ):
            with self.subTest(command=command_class.__module__):
                dests = {action.dest for action in self._parser(command_class)._actions}
                self.assertIn('allow_pre_contract_history', dests)

    def test_default_start_date_is_the_contract_not_the_raw_floor(self):
        from apps.prediction.management.commands.rebuild_lightgbm_pipeline import Command
        from apps.prediction.management.commands.rebuild_lstm_pipeline import Command as LSTMCommand

        self.assertEqual(Command._default_start_date(), STOCK_TRAINING_START)
        self.assertEqual(LSTMCommand._default_start_date(), STOCK_TRAINING_START)

    def test_research_command_stays_on_the_legacy_panel_by_default(self):
        # measure_etf_coverage_bias exists to study the pre-contract era, so it is
        # deliberately outside the contract. If this default ever moves, the tool
        # has stopped measuring the thing it was written to measure.
        from apps.etf_rotation.management.commands.measure_etf_coverage_bias import Command

        defaults = {
            action.dest: action.default
            for action in self._parser(Command)._actions
        }
        self.assertEqual(defaults['start'], '2016-01-29')


class SystemEndpointsTests(SimpleTestCase):

    def test_date_contract_reports_the_constants_not_copies(self):
        from apps.core.views import _contract_payload

        payload = _contract_payload()
        by_key = {entry['key']: entry for entry in payload['asset_classes']}
        self.assertEqual(
            by_key[ASSET_STOCKS]['training_start'], STOCK_TRAINING_START.isoformat(),
        )
        self.assertEqual(
            by_key[ASSET_ETF]['backtest_start'], ETF_BACKTEST_START.isoformat(),
        )
        self.assertEqual(payload['escape_hatch_flag'], PRE_CONTRACT_FLAG)

    def test_raw_bounds_are_labelled_as_observations(self):
        from apps.core import date_contract
        from apps.core.views import _contract_payload

        self.assertIn('Descriptive only', date_contract.__doc__)
        self.assertIn('observation', _contract_payload()['note'].lower())
