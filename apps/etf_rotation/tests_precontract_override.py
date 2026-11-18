"""The pre-contract blocker fix must actually reach the engine call (#136).

The grid's three starts (2016/2017/2018) all precede ETF_BACKTEST_START = 2021-01-13, so
without `allow_pre_contract_history=True` the engine raises ContractBoundaryError and the
pre-registered grid cannot run at all. Reading the source and seeing the kwarg is not
verification; this drives the command with the engine call mocked and asserts the kwarg is
passed for every cell.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

PREREG = Path(__file__).resolve().parents[2] / 'docs/reports/preregistration/etf-icir-verdict-2026-11-18.md'


class PreContractOverrideTests(SimpleTestCase):
    def test_every_cell_passes_allow_pre_contract_history(self):
        self.assertTrue(PREREG.exists(), f'missing pre-registration: {PREREG}')
        seen = []

        def fake_call_command(name, **kwargs):
            if name == 'run_etf_rotation_backtest':
                seen.append(kwargs)

        fake_row = {
            'id': 'smoke',
            'context': {'model_type': 'RULE', 'panel_start': '2016-01-29'},
            'results': {'blocks': [], 'sharpe': {}, 'max_drawdown': {}},
        }
        with TemporaryDirectory() as out_dir:
            with patch('apps.etf_rotation.management.commands.measure_etf_rotation_sensitivity.call_command',
                       side_effect=fake_call_command), \
                 patch('apps.etf_rotation.management.commands.measure_etf_rotation_sensitivity.evidence.read_measurements',
                       return_value=[fake_row]):
                call_command(
                    'measure_etf_rotation_sensitivity',
                    preregistration=str(PREREG),
                    panel_starts='2016-01-29,2017-01-03,2018-01-02',
                    arms='rule',
                    baseline_arm='rule',
                    out_dir=out_dir,
                    stdout=open('/dev/null', 'w'),
                )
        self.assertEqual(len(seen), 3, f'expected one engine call per panel start, got {len(seen)}')
        for kwargs in seen:
            self.assertIs(kwargs.get('allow_pre_contract_history'), True)
            self.assertEqual(kwargs.get('frequency'), 'BIWEEKLY')
            self.assertEqual(kwargs.get('max_positions'), 3)
            self.assertEqual(kwargs.get('momentum_lookback'), 120)

    def test_the_contract_really_would_raise_without_the_override(self):
        from datetime import date

        from apps.core.date_contract import (
            ASSET_ETF, STAGE_BACKTEST, ContractBoundaryError, enforce_date_contract,
        )
        with self.assertRaises(ContractBoundaryError):
            enforce_date_contract(
                asset_class=ASSET_ETF, stage=STAGE_BACKTEST,
                requested_start=date(2016, 1, 29), allow_pre_contract=False,
            )
        resolved = enforce_date_contract(
            asset_class=ASSET_ETF, stage=STAGE_BACKTEST,
            requested_start=date(2016, 1, 29), allow_pre_contract=True,
        )
        self.assertEqual(resolved, date(2016, 1, 29))
