"""Run a pre-registered sensitivity grid and report how often an arm replicates.

The question this answers is not "does arm X pass" but "does arm X pass *again* when only a
nuisance choice changes". A single pass on one panel start is one draw from a distribution of
boundary placements; the pre-registration file is what keeps the second half of the grid from
being chosen after seeing the first half.

Two guards, both deliberate:

* ``--preregistration`` is required, and every context that will run has to appear in that
  file as ``<arm>@<panel-start>``. A grid whose cells were picked after the fact is a search,
  not a test, so the command refuses rather than warns.
* The baseline arm must be in the grid: a "win" means beating RULE on the same panel start,
  not beating a number remembered from another report.

Each cell is measured through ``run_etf_rotation_backtest --cpcv-retrain`` (the same path a
human would use), and its numbers are read back from the measurement ledger rather than
parsed out of stdout. The ledger entry is the machine-readable record; this command only
aggregates.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.etf_rotation import evidence

#: Arm -> the engine/model combination it stands for. Embargo is 20 for a sequence model
#: because its input window is 20 sessions long (see report section 11.4).
ARMS = {
    'rule': {'model': 'RULE', 'hybrid': False, 'embargo': 5, 'fold_plan': 'cpcv'},
    'lgb-select': {'model': 'LIGHTGBM', 'hybrid': False, 'embargo': 5, 'fold_plan': 'cpcv'},
    'lgb-veto': {'model': 'LIGHTGBM', 'hybrid': True, 'embargo': 5, 'fold_plan': 'cpcv'},
    'lstm-select': {'model': 'LSTM', 'hybrid': False, 'embargo': 20, 'fold_plan': 'cpcv'},
    'lstm-veto': {'model': 'LSTM', 'hybrid': True, 'embargo': 20, 'fold_plan': 'cpcv'},
    # Rolling variants: same validation calendar as the expanding plan, fixed-length training
    # window. A baseline arm has to be measured on the same plan as the arm it is compared to,
    # so each plan carries its own ``rule`` entry.
    'rule-rolling': {'model': 'RULE', 'hybrid': False, 'embargo': 5, 'fold_plan': 'rolling'},
    'lgb-select-rolling': {
        'model': 'LIGHTGBM', 'hybrid': False, 'embargo': 5, 'fold_plan': 'rolling',
    },
}

#: A drawdown median within this margin of the baseline still counts as "not worse".
DRAWDOWN_MARGIN = 0.02


def prereg_digest(text: str) -> str:
    """The ledger's own digest, so a report name and a ledger row cannot disagree."""
    return evidence.preregistration_digest(text)


def context_slug(arm: str, panel_start: str) -> str:
    return f'{arm}@{panel_start}'


def leg_verdict(arm: dict, baseline: dict) -> dict:
    """Which of the three pre-registered legs this draw wins."""
    arm_sharpe = (arm.get('sharpe') or {}).get('median')
    base_sharpe = (baseline.get('sharpe') or {}).get('median')
    arm_positive = (arm.get('sharpe') or {}).get('positive_share')
    base_positive = (baseline.get('sharpe') or {}).get('positive_share')
    arm_dd = (arm.get('max_drawdown') or {}).get('median')
    base_dd = (baseline.get('max_drawdown') or {}).get('median')

    sharpe_ok = None if None in (arm_sharpe, base_sharpe) else arm_sharpe > base_sharpe
    positive_ok = None if None in (arm_positive, base_positive) else arm_positive >= base_positive
    drawdown_ok = None if None in (arm_dd, base_dd) else arm_dd <= base_dd + DRAWDOWN_MARGIN
    passed = all(flag for flag in (sharpe_ok, positive_ok, drawdown_ok) if flag is not None) and any(
        flag is not None for flag in (sharpe_ok, positive_ok, drawdown_ok)
    )
    return {
        'sharpe': sharpe_ok, 'positive_blocks': positive_ok, 'drawdown': drawdown_ok,
        'passed': passed,
    }


class Command(BaseCommand):
    help = 'Run a pre-registered sensitivity grid over panel starts and arms.'

    def add_arguments(self, parser):
        parser.add_argument('--preregistration', required=True, metavar='PATH',
                            help='Markdown file listing every context that may run, as '
                                 '"<arm>@<panel-start>". Its hash is recorded with the results.')
        parser.add_argument('--panel-starts', required=True,
                            help='Comma-separated panel starts, e.g. 2016-01-29,2017-01-03.')
        parser.add_argument('--arms', default='rule,lgb-veto,lstm-veto',
                            help=f'Comma-separated arms from {sorted(ARMS)}.')
        parser.add_argument('--baseline-arm', default='rule',
                            help='Arm every other arm is compared against (default rule).')
        parser.add_argument('--end', default='2023-12-31', help='Panel end (ISO).')
        parser.add_argument('--blocks', type=int, default=6, help='Blocks per split (default 6).')
        parser.add_argument('--rolling-train-days', type=int, default=750,
                            help='Training window for the rolling arms (default 750 sessions).')
        parser.add_argument('--out-dir', default=None,
                            help='Where to write the markdown table. Default: '
                                 'docs/reports/sensitivity.')

    def handle(self, *args, **options):
        path = Path(options['preregistration'])
        if not path.is_file():
            raise CommandError(f'pre-registration file not found: {path}')
        text = path.read_text(encoding='utf-8')
        digest = prereg_digest(text)

        arms = [arm.strip() for arm in str(options['arms']).split(',') if arm.strip()]
        starts = [start.strip() for start in str(options['panel_starts']).split(',') if start.strip()]
        baseline_arm = str(options['baseline_arm']).strip()
        unknown = [arm for arm in arms if arm not in ARMS]
        if unknown:
            raise CommandError(f'unknown arm(s) {unknown}; known: {sorted(ARMS)}')
        if baseline_arm not in arms:
            raise CommandError(
                f'--baseline-arm {baseline_arm} is not in --arms; a win has to be measured '
                'on the same panel start, not remembered from another report.'
            )

        missing = [
            context_slug(arm, start) for arm in arms for start in starts
            if context_slug(arm, start) not in text
        ]
        if missing:
            raise CommandError(
                'these contexts are not declared in the pre-registration file: '
                f'{missing}. Add them before running, or drop them from the grid -- choosing '
                'cells after seeing the results is a search, not a test.'
            )

        self.stdout.write(
            f'sensitivity grid: {len(arms)} arm(s) x {len(starts)} panel start(s) = '
            f'{len(arms) * len(starts)} cell(s); pre-registration {digest}'
        )
        results = {}
        for start in starts:
            for arm in arms:
                spec = ARMS[arm]
                call_command(
                    'run_etf_rotation_backtest',
                    start=start, end=options['end'], model=spec['model'],
                    frequency='BIWEEKLY', max_positions=3, momentum_lookback=120,
                    absolute_momentum_filter=False, weighting='equal',
                    cpcv_blocks=options['blocks'], cpcv_test_blocks=1,
                    cpcv_embargo=spec['embargo'], cpcv_retrain=True,
                    cpcv_hybrid=spec['hybrid'], fold_plan=spec.get('fold_plan', 'cpcv'),
                    rolling_train_days=options['rolling_train_days'],
                    # Every cell of a pre-registered grid is governed by that registration, so
                    # the digest travels down to the ledger row the cell produces.
                    preregistration=str(path), stdout=io.StringIO(),
                )
                record = evidence.read_measurements()[-1]
                results[(arm, start)] = {
                    'context': record.get('context') or {},
                    'results': record.get('results') or {},
                    'evidence_id': record.get('id', ''),
                }

        verdicts = {}
        for (arm, start), payload in results.items():
            if arm == baseline_arm:
                continue
            baseline = results.get((baseline_arm, start), {}).get('results') or {}
            verdicts[(arm, start)] = leg_verdict(payload['results'], baseline)

        lines = [
            f'# Sensitivity grid `{digest}`',
            '',
            f'Panel starts: {", ".join(starts)}. End: {options["end"]}. '
            f'Blocks: {options["blocks"]}. Baseline arm: `{baseline_arm}`.',
            f'Pre-registration: `{path}` (sha256 {digest}).',
            '',
            '| arm | panel start | median Sharpe | positive blocks | maxDD median | legs won | passed |',
            '| :--- | :--- | ---: | ---: | ---: | :--- | :--- |',
        ]
        for (arm, start), payload in results.items():
            distribution = payload['results']
            verdict = verdicts.get((arm, start))
            sharpe = (distribution.get('sharpe') or {}).get('median')
            positive = (distribution.get('sharpe') or {}).get('positive_share')
            drawdown = (distribution.get('max_drawdown') or {}).get('median')
            legs = (
                '-'
                if verdict is None
                else '/'.join(
                    'win' if verdict[key] else ('lose' if verdict[key] is False else 'n/a')
                    for key in ('sharpe', 'positive_blocks', 'drawdown')
                )
            )
            lines.append(
                f'| {arm} | {start} | '
                f'{("%.4f" % sharpe) if isinstance(sharpe, (int, float)) else "n/a"} | '
                f'{("%.2f%%" % (positive * 100)) if isinstance(positive, (int, float)) else "n/a"} | '
                f'{("%.2f%%" % (drawdown * 100)) if isinstance(drawdown, (int, float)) else "n/a"} | '
                f'{legs} | {"yes" if verdict and verdict["passed"] else "no"} |'
            )

        lines.extend(['', '## Replication', ''])
        for arm in arms:
            if arm == baseline_arm:
                continue
            arm_verdicts = [verdicts[(arm, start)] for start in starts if (arm, start) in verdicts]
            wins = sum(1 for verdict in arm_verdicts if verdict['passed'])
            lines.append(
                f'- `{arm}`: passed in **{wins}/{len(arm_verdicts)}** panel start(s). '
                + ('Effect replicates.' if wins == len(arm_verdicts) and wins > 1
                   else 'Not reproduced across contexts.')
            )
        report = '\n'.join(lines) + '\n'

        out_dir = options['out_dir'] or os.path.join(
            str(settings.BASE_DIR), 'docs/reports/sensitivity'
        )
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f'{path.stem}-{digest}.md')
        Path(out_path).write_text(report, encoding='utf-8')
        self.stdout.write(report)
        self.stdout.write(f'written: {out_path}')
