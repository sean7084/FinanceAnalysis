"""Sweep the ETF LSTM hyperparameters over the tuning window and pick one candidate.

CLI-only by design (Phase C): this command is how a sweep is *run*, not how one is
presented, so it writes no serializer-facing table and needs no new endpoint.

**Protocol.** Tuning window = data through **2023-12-31** (``--end`` defaults to it and a
later value is refused unless ``--allow-holdout-overlap`` says you know what you are doing).
The locked holdout 2024-01-01..2026-10-20 is where a candidate is finally judged, and the
RULE grid winner was already selected there, so a sweep that read those dates would be
selecting on the evidence it is supposed to be scored by. Fold construction, the purge, and
the fold audit all come from ``apps.etf_rotation.walk_forward`` through
``train_lstm.train_walk_forward`` -- reimplemented here in none of its parts.

**Selection** = ``aggregate['mean_rank_ic']`` first, then ``aggregate['folds_with_positive_ic']``.
Both are already computed by the trainer; nothing here re-derives an IC. Portfolio metrics are
deliberately not swept -- a tuning row has no equity curve, and its metric columns stay NULL so
a rank IC can never be mis-read as alpha.

**Trials run sequentially, in this process.** The plan's wording was "sequentially through the
``train-lstm`` queue"; this is the stricter reading of the same constraint. That queue's worker
comes up with ``--concurrency 2`` (Phase 0), so two dispatched trials may hold VRAM at once,
whereas one in-process loop holds exactly one allocation at a time. The binding constraint is
VRAM, not GPU availability, and the GPU is always free.

**Contamination discipline.** Every ledger row is named ``tune-lstm-*`` and carries
``report['tuning_only']``; every ``ModelVersion`` is registered with ``activate=False`` and a
``metadata['sweep']`` provenance dict, and -- because ``sweep`` is supplied -- never gets a
``metadata['pooled_holdout']`` key, so a sweep row cannot be mistaken for out-of-sample evidence
or weighted into an ensemble. The activation guard at the end of ``handle`` re-reads the active
set and fails the command if the sweep changed it.

**Resumable.** One ``ETFRotationBacktest`` row per configuration keyed by its name; a COMPLETED
row is re-read and skipped unless ``--force``. A row whose stored fingerprint no longer matches
the requested window/protocol is re-run rather than reused, because the name alone does not
encode ``--start``/``--end``/``--folds``.

Examples::

    python manage.py sweep_etf_rotation_lstm --dry-run --plan
    python manage.py sweep_etf_rotation_lstm --limit 2
    python manage.py sweep_etf_rotation_lstm
    python manage.py sweep_etf_rotation_lstm --force --top 8
"""

from __future__ import annotations

import hashlib
import importlib
import itertools
import json
import math
import time
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Min
from django.utils import timezone

from apps.etf_rotation.models import ETFFeatureSnapshot, ETFRotationBacktest

#: The model type the sweep tunes. Kept as a literal so ``--plan`` works on a host without
#: torch, where importing ``train_lstm`` for ``MODEL_TYPE_LABEL`` would fail.
MODEL_TYPE_LABEL = 'ETF_LSTM'

#: Last date the sweep may look at. The holdout starts the day after.
TUNING_WINDOW_END = date(2023, 12, 31)
LOCKED_HOLDOUT_START = date(2024, 1, 1)

#: Prefix that marks every row this command writes, as ``grid-rule-`` marks the RULE sweep.
TUNE_NAME_PREFIX = 'tune-lstm-'

#: The five axes. 2 x 2 x 2 x 2 x 2 = 32 trials, asserted as a product below the way
#: ``GRID_EXPECTED_COMBOS`` does for the RULE grid.
#:
#: ``loss`` replaces the plan's ``objective {binary, lambdarank}`` axis, which are LightGBM
#: objectives: the LSTM's equivalent knob is ``params['loss']``, and ``train_lstm._build_config``
#: validates exactly the two values below. The learning rate and epoch count move as one axis
#: because the plan asked for "a small learning-rate/epochs variation" and handing a second pair
#: to only some of the 16 capacity/loss combinations would be an unprincipled selection; the
#: product then lands on a clean power of two.
SWEEP_HIDDEN_SIZES = (64, 128)
SWEEP_NUM_LAYERS = (1, 2)
SWEEP_DROPOUTS = (0.2, 0.35)
SWEEP_LOSSES = ('bce_top_bucket', 'pairwise_rank')
SWEEP_LR_EPOCHS = ((1e-3, 50), (3e-4, 80))
SWEEP_EXPECTED_COMBOS = 32

#: ``params`` keys the trainer's published signature does not carry.
DEFAULT_SEQUENCE_LENGTH = 20
DEFAULT_FOLDS = 6


def sweep_combinations() -> list[dict[str, Any]]:
    """The 32 LSTM configurations, in a deterministic order."""
    combos = [
        {'hidden_size': hidden, 'num_layers': layers, 'dropout': dropout,
         'loss': loss, 'learning_rate': learning_rate, 'epochs': epochs}
        for hidden, layers, dropout, loss, (learning_rate, epochs) in itertools.product(
            SWEEP_HIDDEN_SIZES, SWEEP_NUM_LAYERS, SWEEP_DROPOUTS, SWEEP_LOSSES, SWEEP_LR_EPOCHS,
        )
    ]
    product = (len(SWEEP_HIDDEN_SIZES) * len(SWEEP_NUM_LAYERS) * len(SWEEP_DROPOUTS)
               * len(SWEEP_LOSSES) * len(SWEEP_LR_EPOCHS))
    if len(combos) != product or product != SWEEP_EXPECTED_COMBOS:
        raise CommandError(
            f'Sweep dimension product is {product} ({len(combos)} combinations), not the '
            f'{SWEEP_EXPECTED_COMBOS} the plan specifies. Check the SWEEP_* constants.'
        )
    return combos


def _dropout_label(value: float) -> str:
    return f'{int(round(float(value) * 100)):03d}'


def _lr_label(value: float) -> str:
    """``1e-3`` for a name column; the value itself is what the trainer receives."""
    exponent = int(math.floor(math.log10(float(value))))
    mantissa = int(round(float(value) / (10.0 ** exponent)))
    return f'{mantissa}e{exponent}'


def trial_name(combo: dict[str, Any]) -> str:
    """A name that shows the whole configuration, the way ``grid_name`` does."""
    loss_short = 'bce' if combo['loss'] == 'bce_top_bucket' else 'pair'
    return (
        f'{TUNE_NAME_PREFIX}h{combo["hidden_size"]}-l{combo["num_layers"]}'
        f'-d{_dropout_label(combo["dropout"])}-{loss_short}'
        f'-lr{_lr_label(combo["learning_rate"])}-e{combo["epochs"]}'
    )


def protocol_knobs(options: dict[str, Any]) -> dict[str, Any]:
    """The fold-plan knobs that are not per-trial grid axes.

    They belong in the fingerprint: a sweep re-run at ``--folds 8`` over the same names is a
    different experiment, and reusing the ``--folds 6`` rows would rank incomparable numbers
    against each other.
    """
    return {
        'folds': int(options['folds']),
        'horizon_days': options['horizon'],
        'min_train_days': options['min_train_days'],
        'sequence_length': int(options['sequence_length']),
    }


def trial_fingerprint(combo: dict[str, Any], window: dict[str, str], knobs: dict[str, Any]) -> str:
    """Identity of (configuration, window, protocol), same shape as ``BacktestConfig.fingerprint``."""
    payload = {'model_type': MODEL_TYPE_LABEL, 'sweep': 'lstm', **combo, **window, **knobs}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode())
    return digest.hexdigest()[:16]


def trial_parameters(combo: dict[str, Any], name: str, fingerprint: str,
                     window: dict[str, str], knobs: dict[str, Any]) -> dict[str, Any]:
    return {
        'sweep': 'lstm',
        'trial': name,
        'fingerprint': fingerprint,
        'config': combo,
        'window': window,
        'protocol': knobs,
        'grid_axes': {
            'hidden_size': list(SWEEP_HIDDEN_SIZES), 'num_layers': list(SWEEP_NUM_LAYERS),
            'dropout': list(SWEEP_DROPOUTS), 'loss': list(SWEEP_LOSSES),
            'learning_rate_epochs': [[lr, ep] for lr, ep in SWEEP_LR_EPOCHS],
        },
    }


def tuning_only_report(window: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    """What goes in ``report['tuning_only']`` -- the same treatment the ``INSAMPLE-`` rows got."""
    aggregate = result.get('aggregate') or {}
    return {
        'window': window,
        'fold_ics': [((fold.get('metrics') or {}).get('rank_ic'))
                     for fold in (result.get('folds') or [])],
        'mean_rank_ic': aggregate.get('mean_rank_ic'),
        'mean_icir': aggregate.get('mean_icir'),
        'folds_with_positive_ic': aggregate.get('folds_with_positive_ic'),
        'folds_trained': aggregate.get('folds_trained'),
        'folds_planned': result.get('n_folds'),
        'selection_rule': 'aggregate.mean_rank_ic, then aggregate.folds_with_positive_ic',
        'note': ('Tuning ledger row: no portfolio simulation was run, so every metric column is '
                 'NULL and the values here are walk-forward rank ICs inside the tuning window. '
                 'Not evidence about live performance; the locked holdout is judged separately.'),
    }


def _trainer():
    """``apps.etf_rotation.train_lstm``, or a CommandError explaining why it is missing.

    Imported lazily because the module imports torch at module level, so a torch-less host
    (CI, the Docker image) can still run ``--plan`` and ``--dry-run``.
    """
    try:
        return importlib.import_module('apps.etf_rotation.train_lstm')
    except ImportError as exc:  # pragma: no cover - depends on the host, not the code
        raise CommandError(
            'A live sweep needs apps.etf_rotation.train_lstm, which imports torch. '
            f'Cannot train from this interpreter: {exc}. Use --dry-run --plan to see the grid.'
        ) from exc


def parse_iso_date(label: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'{label} must be ISO format YYYY-MM-DD, got {value!r}.') from exc


def resolve_window(options: dict[str, Any]) -> dict[str, str]:
    """The tuning window as ISO strings, with ``start`` resolved against the panel.

    ``--start`` defaults to the earliest eligible snapshot so that a resumed sweep keys the
    same fingerprints: that minimum is stable once the panel is built, and passing the resolved
    pair to the trainer keeps the ledger row and the fold plan in agreement.
    """
    end = parse_iso_date('--end', options['end']) if options['end'] else TUNING_WINDOW_END
    start = parse_iso_date('--start', options['start']) if options['start'] else None
    if start is None:
        start = ETFFeatureSnapshot.objects.filter(is_eligible=True).aggregate(
            first=Min('date'))['first']
    if start is None:
        raise CommandError(
            'ETFFeatureSnapshot has no is_eligible rows, so the tuning window has no start. '
            'Build the feature snapshots first (compute_etf_features_daily).'
        )
    if end < start:
        raise CommandError(f'--end {end} is before the panel start {start}.')
    if end >= LOCKED_HOLDOUT_START and not options['allow_holdout_overlap']:
        raise CommandError(
            f'--end {end} reaches into the locked holdout that starts {LOCKED_HOLDOUT_START}. '
            'Selecting on it is the contamination this protocol exists to avoid; pass '
            '--allow-holdout-overlap only if the holdout has already been spent deliberately.'
        )
    return {'tuning_start': start.isoformat(), 'tuning_end': end.isoformat()}


class Command(BaseCommand):
    help = 'Bounded, resumable LSTM hyperparameter sweep over the ETF tuning window.'

    def add_arguments(self, parser):
        parser.add_argument('--start', help='Tuning window start (ISO). Default: earliest eligible snapshot date.')
        parser.add_argument('--end', help=f'Tuning window end (ISO). Default: {TUNING_WINDOW_END.isoformat()}.')
        parser.add_argument('--folds', type=int, default=DEFAULT_FOLDS,
                            help='Walk-forward steps per trial (default 6). Part of the fingerprint.')
        parser.add_argument('--horizon', type=int,
                            help='Label horizon in sessions. Default: settings.ETF_LABEL_HORIZON_DAYS.')
        parser.add_argument('--min-train-days', type=int,
                            help='Minimum dates in the first training window. Default: settings.ETF_MIN_TRAIN_DAYS.')
        parser.add_argument('--sequence-length', type=int, default=DEFAULT_SEQUENCE_LENGTH,
                            help='Bars per sequence (default 20). Part of the fingerprint.')
        parser.add_argument('--limit', type=int,
                            help='Run only the first N configurations, for a smoke test.')
        parser.add_argument('--top', type=int, default=10, help='Rows in the ranked table (default 10).')
        parser.add_argument('--force', action='store_true',
                            help='Re-run configurations whose row is already COMPLETED.')
        parser.add_argument('--plan', action='store_true',
                            help='Print every configuration, name and fingerprint; write nothing.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Resolve the window, report the ledger state, train nothing, write nothing.')
        parser.add_argument('--allow-holdout-overlap', action='store_true',
                            help=f'Let --end reach past {LOCKED_HOLDOUT_START} (it is refused otherwise).')

    def handle(self, *args, **options):
        combos = sweep_combinations()
        knobs = protocol_knobs(options)
        if options['limit'] and options['limit'] < len(combos):
            requested = options['limit']
            combos = combos[:requested]
            self.stdout.write(self.style.WARNING(
                f'--limit {requested}: running a subset, not the full {SWEEP_EXPECTED_COMBOS}-trial grid.'
            ))

        if options['plan']:
            self._render_plan(combos, knobs, options)
            if not options['dry_run']:
                return
        if options['dry_run']:
            self._render_dry_run(combos, knobs, options)
            return
        self._run_sweep(combos, knobs, options)

    # -- read-only branches ---------------------------------------------------
    def _render_plan(self, combos, knobs, options) -> None:
        self.stdout.write(
            f'planned LSTM trials: {len(combos)} = {len(SWEEP_HIDDEN_SIZES)} hidden_size x '
            f'{len(SWEEP_NUM_LAYERS)} num_layers x {len(SWEEP_DROPOUTS)} dropout x '
            f'{len(SWEEP_LOSSES)} loss x {len(SWEEP_LR_EPOCHS)} (learning_rate, epochs)'
        )
        self.stdout.write(
            f'protocol: folds={knobs["folds"]} horizon_days={knobs["horizon_days"] or "settings"} '
            f'min_train_days={knobs["min_train_days"] or "settings"} '
            f'sequence_length={knobs["sequence_length"]}'
        )
        self.stdout.write(
            f'losses: {SWEEP_LOSSES} -- this is the LSTM equivalent of the plan\'s '
            '"objective {binary, lambdarank}" axis, which belongs to LightGBM.'
        )
        self.stdout.write(
            f'tuning window: --start {options["start"] or "(earliest eligible snapshot)"} .. '
            f'--end {options["end"] or TUNING_WINDOW_END.isoformat()}; the locked holdout from '
            f'{LOCKED_HOLDOUT_START.isoformat()} is never read by a trial.'
        )
        self.stdout.write(
            'fold plan: built inside train_lstm.train_walk_forward over label-complete dates '
            '(walk_forward.expanding_folds + purge_train_dates + audit_folds); it needs the '
            'panel, so --plan does not print it. train_lstm exposes no plan_walk_forward.'
        )
        self.stdout.write(
            'cfg-fp is the fingerprint with the window left unresolved; the value stored on a '
            'ledger row also covers the resolved --start/--end, so it differs at run time.'
        )
        ledger = dict(
            ETFRotationBacktest.objects.filter(name__startswith=TUNE_NAME_PREFIX)
            .values_list('name', 'status')
        )
        unresolved = {'tuning_start': '', 'tuning_end': ''}
        for index, combo in enumerate(combos, start=1):
            name = trial_name(combo)
            fingerprint = trial_fingerprint(combo, unresolved, knobs)
            self.stdout.write(
                f'[{index:>2}/{len(combos)}] {name} cfg-fp={fingerprint} '
                f'ledger={ledger.get(name, "new")} {json.dumps(combo, sort_keys=True)}'
            )
        self.stdout.write(self.style.SUCCESS(
            f'--plan: {len(combos)} configurations listed; '
            f'{len(ledger)} tune-lstm- ledger row(s) already exist; nothing trained.'
        ))

    def _render_dry_run(self, combos, knobs, options) -> None:
        try:
            window = resolve_window(options)
        except CommandError as exc:
            self.stdout.write(self.style.WARNING(f'dry run: {exc}'))
            return
        self.stdout.write(
            f'resolved tuning window: {window["tuning_start"]}..{window["tuning_end"]} '
            f'({len(combos)} trials x {knobs["folds"]} folds)'
        )
        rows = ETFRotationBacktest.objects.filter(name__startswith=TUNE_NAME_PREFIX)
        counts: dict[str, int] = {}
        for status in ('PENDING', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED'):
            counts[status] = int(rows.filter(status=status).count())
        self.stdout.write(
            'ledger rows by status: ' + ', '.join(f'{key}={value}' for key, value in counts.items())
            + f' (of {rows.count()} total)'
        )
        active_before = list(_active_lstm_versions())
        self.stdout.write(
            'active ETF_LSTM ModelVersion(s) before any sweep: '
            + (', '.join(active_before) if active_before else '(none)')
            + ' -- a sweep must leave this set exactly as it found it.'
        )
        eligible = ETFFeatureSnapshot.objects.filter(is_eligible=True,
                                                     date__lte=window['tuning_end']).count()
        self.stdout.write(
            f'eligible snapshots in the window: {eligible} rows; dry run trains nothing and writes nothing.'
        )

    # -- the sweep ------------------------------------------------------------
    def _run_sweep(self, combos, knobs, options) -> None:
        # Resolve the window before importing torch. An ``--end`` that reaches into the locked
        # holdout, or a panel with no eligible rows, is operator error; importing torch costs
        # seconds and hundreds of megabytes before it could even be reported.
        window = resolve_window(options)
        trainer = _trainer()
        losses = (trainer.LOSS_BCE, trainer.LOSS_PAIRWISE)
        for combo in combos:
            if combo['loss'] not in losses:
                raise CommandError(
                    f"sweep axis loss='{combo['loss']}' is not one of {losses}; "
                    'the grid constants and train_lstm have drifted apart.'
                )
        source_fingerprint = trial_fingerprint(combos[0], window, knobs) if combos else ''
        self.stdout.write(
            f'tuning window {window["tuning_start"]}..{window["tuning_end"]}; '
            f'{len(combos)} trial(s) x {knobs["folds"]} folds, run sequentially in this process. '
            f'(fingerprint of the first trial: {source_fingerprint})'
        )

        active_before = set(_active_lstm_versions())
        rows: list[dict[str, Any]] = []
        failures: list[str] = []
        skipped = 0
        started = time.monotonic()

        for index, combo in enumerate(combos, start=1):
            name = trial_name(combo)
            fingerprint = trial_fingerprint(combo, window, knobs)
            parameters = trial_parameters(combo, name, fingerprint, window, knobs)
            row, created = ETFRotationBacktest.objects.get_or_create(
                name=name,
                defaults={
                    'start_date': date.fromisoformat(window['tuning_start']),
                    'end_date': date.fromisoformat(window['tuning_end']),
                    'model_type': ETFRotationBacktest.ModelType.LSTM,
                    'status': ETFRotationBacktest.Status.PENDING,
                    'parameters': parameters,
                },
            )
            if not created:
                stale = str((row.parameters or {}).get('fingerprint') or '') != fingerprint
                if row.status == ETFRotationBacktest.Status.COMPLETED and not stale and not options['force']:
                    skipped += 1
                    prior = _row_result(row, combo)
                    rows.append(prior)
                    self.stdout.write(
                        f'[{index}/{len(combos)}] {name}: already COMPLETED (id={row.pk}), skipped '
                        f'mean_rank_ic={_num(prior["metrics"].get("mean_rank_ic"))}'
                    )
                    continue
                if stale:
                    self.stdout.write(self.style.WARNING(
                        f'[{index}/{len(combos)}] {name}: stored fingerprint '
                        f'{(row.parameters or {}).get("fingerprint")} does not match {fingerprint}; '
                        'the window or protocol changed, so this trial is re-run.'
                    ))
                row.parameters = parameters
                row.start_date = date.fromisoformat(window['tuning_start'])
                row.end_date = date.fromisoformat(window['tuning_end'])
                row.model_type = ETFRotationBacktest.ModelType.LSTM
                row.status = ETFRotationBacktest.Status.RUNNING
                row.error_message = ''
                row.started_at = timezone.now()
                row.save(update_fields=['parameters', 'start_date', 'end_date', 'model_type',
                                        'status', 'error_message', 'started_at', 'updated_at'])

            self.stdout.write(f'[{index}/{len(combos)}] {name}: training {json.dumps(combo, sort_keys=True)}')
            trial_started = time.monotonic()
            try:
                result = trainer.train_walk_forward(
                    model_type_label=trainer.MODEL_TYPE_LABEL,
                    start_date=date.fromisoformat(window['tuning_start']),
                    end_date=date.fromisoformat(window['tuning_end']),
                    n_folds=knobs['folds'], sequence_length=knobs['sequence_length'],
                    hidden_size=int(combo['hidden_size']), num_layers=int(combo['num_layers']),
                    dropout=float(combo['dropout']), epochs=int(combo['epochs']),
                    learning_rate=float(combo['learning_rate']),
                    horizon_days=knobs['horizon_days'], min_train_days=knobs['min_train_days'],
                    version_tag=f't-{fingerprint[:8]}',
                    params={'loss': combo['loss']},
                    activate=False,
                    sweep={
                        'campaign': 'sweep_etf_rotation_lstm',
                        'trial': name,
                        'fingerprint': fingerprint,
                        'grid': combo,
                        'tuning_window': window,
                        'protocol': knobs,
                        'trials_in_grid': len(combos),
                        'activated': False,
                    },
                )
            except Exception as exc:  # one bad trial must not cost the other 31
                failures.append(f'{name}: {type(exc).__name__}: {exc}')
                self.stdout.write(self.style.ERROR(
                    f'[{index}/{len(combos)}] {name} FAILED: {exc}'))
                row.status = ETFRotationBacktest.Status.FAILED
                row.error_message = f'{type(exc).__name__}: {exc}'[:2000]
                row.completed_at = timezone.now()
                row.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
                continue

            status = result.get('status')
            if status != 'success':
                reason = result.get('reason', status)
                failures.append(f'{name}: {status}: {reason}')
                self.stdout.write(self.style.WARNING(
                    f'[{index}/{len(combos)}] {name}: {status} - {reason}'))
                row.status = ETFRotationBacktest.Status.FAILED
                row.error_message = f'{status}: {reason}'[:2000]
                row.completed_at = timezone.now()
                row.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
                continue

            report = dict(row.report or {})
            report['tuning_only'] = tuning_only_report(window, result)
            parameters = dict(parameters)
            parameters['model_version'] = result['model_version']
            parameters['model_version_id'] = result.get('model_version_id')
            parameters['artifact_path'] = result.get('artifact_path')
            row.parameters = parameters
            row.report = report
            row.status = ETFRotationBacktest.Status.COMPLETED
            row.completed_at = timezone.now()
            row.save(update_fields=['parameters', 'report', 'status', 'completed_at', 'updated_at'])

            aggregate = result.get('aggregate') or {}
            rows.append({
                'name': name, 'combo': combo, 'id': row.pk, 'fingerprint': fingerprint,
                'metrics': {
                    'mean_rank_ic': aggregate.get('mean_rank_ic'),
                    'mean_icir': aggregate.get('mean_icir'),
                    'folds_with_positive_ic': aggregate.get('folds_with_positive_ic'),
                    'folds_trained': aggregate.get('folds_trained'),
                },
                'fold_ics': report['tuning_only']['fold_ics'],
                'model_version': result['model_version'],
                'model_version_id': result.get('model_version_id'),
                'ledger': 'trained',
            })
            self.stdout.write(
                f"[{index}/{len(combos)}] {name}: id={row.pk} "
                f"mean_rank_ic={_num(aggregate.get('mean_rank_ic'))} "
                f"positive_folds={aggregate.get('folds_with_positive_ic')}/{aggregate.get('folds_trained')} "
                f"mean_icir={_num(aggregate.get('mean_icir'))} "
                f"version={result['model_version']} ({time.monotonic() - trial_started:.0f}s)"
            )

        elapsed = time.monotonic() - started
        self.stdout.write(self.style.SUCCESS(
            f'sweep finished: {len(rows) - skipped}/{len(combos)} trials have results '
            f'({skipped} reused from persisted rows) in {elapsed / 60:.1f} min; '
            f'{len(failures)} trial(s) produced nothing.'
        ))
        if failures:
            self.stdout.write(self.style.ERROR(
                f'{len(failures)} trial(s) failed: ' + '; '.join(failures[:10])))

        self._render_table(rows, top=max(int(options['top'] or 10), 1), window=window)
        winner = self._select_winner(rows, knobs=knobs)
        if winner is not None:
            self._stamp_winner(winner, rows, window, knobs, len(combos))
        guard = self._activation_guard(active_before)
        if guard is not None:
            raise CommandError(guard)

    # -- rendering ------------------------------------------------------------
    def _render_table(self, rows: list[dict[str, Any]], *, top: int, window: dict[str, str]) -> None:
        if not rows:
            self.stdout.write(self.style.WARNING('no trial produced metrics; nothing to rank.'))
            return
        ranked = sorted(rows, key=_sort_key, reverse=True)
        self.stdout.write(
            f'--- top {min(top, len(ranked))} of {len(ranked)} LSTM trials, tuning window '
            f'{window["tuning_start"]}..{window["tuning_end"]}, ranked by mean fold rank IC then '
            'positive-fold count ---'
        )
        self.stdout.write(
            f'{"#":>3} {"h":>4} {"L":>2} {"drop":>5} {"lr":>7} {"ep":>3} {"loss":>5} '
            f'{"meanIC":>9} {"pos":>6} {"icir":>8} {"status":>8} {"id":>5}'
        )
        for index, row in enumerate(ranked[:top], start=1):
            combo = row['combo']
            metrics = row['metrics']
            positive = metrics.get('folds_with_positive_ic')
            trained = metrics.get('folds_trained')
            self.stdout.write(
                f'{index:>3} {combo["hidden_size"]:>4} {combo["num_layers"]:>2} '
                f'{combo["dropout"]:>5} {combo["learning_rate"]:>7.0e} {combo["epochs"]:>3} '
                f'{_loss_short(combo["loss"]):>5} {_num(metrics.get("mean_rank_ic")):>9} '
                f'{f"{positive}/{trained}" if positive is not None else "n/a":>6} '
                f'{_num(metrics.get("mean_icir")):>8} '
                f'{row["ledger"]:>8} {row.get("id") or 0:>5}'
            )
            self.stdout.write(f'      fold IC: {", ".join(_num(value) for value in (row["fold_ics"] or [])) or "(none)"}')

    def _select_winner(self, rows: list[dict[str, Any]], *, knobs: dict[str, Any]) -> dict[str, Any] | None:
        """Best trial by the stated rule; an all-negative sweep still returns a candidate."""
        scored = [row for row in rows if _sort_key(row) != (-math.inf, -math.inf)]
        if not scored:
            self.stdout.write(self.style.WARNING(
                'no trial reported a mean rank IC, so there is no candidate to promote. '
                'That is an answer, not a bug: this sweep does not owe the LSTM a win.'
            ))
            return None
        winner = max(scored, key=_sort_key)
        runner_up = None
        if len(scored) > 1:
            runner_up = max((row for row in scored if row is not winner), key=_sort_key, default=None)
        self.stdout.write('--- selected candidate ---')
        self.stdout.write(
            f'{winner["name"]}: mean_rank_ic={_num(winner["metrics"].get("mean_rank_ic"))} '
            f'positive_folds={winner["metrics"].get("folds_with_positive_ic")} '
            f'version={winner.get("model_version")} (inactive, by construction)'
        )
        if runner_up is not None:
            self.stdout.write(
                f'runner-up {runner_up["name"]}: mean_rank_ic='
                f'{_num(runner_up["metrics"].get("mean_rank_ic"))} '
                f'positive_folds={runner_up["metrics"].get("folds_with_positive_ic")}'
            )
        self.stdout.write(
            'The selection was made on the tuning window only. Phase D spends the locked '
            'holdout once, on this configuration, and the gate is portfolio alpha vs RULE '
            'plus >=5/6 folds of positive rank IC.'
        )
        self.stdout.write(
            '--- Phase D single-shot retrain (paste; omit sweep= so this row may carry pooled_holdout) ---')
        combo = winner['combo']
        self.stdout.write(
            'The training window must stop at the locked holdout start: a model fitted through '
            f'{LOCKED_HOLDOUT_START.isoformat()} cannot be scored on it. This is the '
            'training-window/benchmark-window overlap that produced the +513.76% row that had to be '
            'relabelled INSAMPLE-.'
        )
        self.stdout.write(
            'DJANGO_READ_DOT_ENV_FILE=True python manage.py shell -c "'
            "from datetime import date; "
            'from apps.etf_rotation import train_lstm as t; '
            't.train_walk_forward('
            f'end_date=date({TUNING_WINDOW_END.year}, {TUNING_WINDOW_END.month}, '
            f'{TUNING_WINDOW_END.day}), n_folds={knobs["folds"]}, '
            f'horizon_days={knobs["horizon_days"]}, sequence_length={knobs["sequence_length"]}, '
            f'hidden_size={combo["hidden_size"]}, num_layers={combo["num_layers"]}, '
            f'dropout={combo["dropout"]}, epochs={combo["epochs"]}, '
            f'learning_rate={combo["learning_rate"]}, '
            f"params={{'loss': '{combo['loss']}'}}, "
            "version_tag='oos-lstm-v2', activate=False)"
            '"'
        )
        self.stdout.write(
            'No sweep= here: this is the one run allowed to export metadata[\'pooled_holdout\']. '
            'activate=False stays until the gate is read — flipping it is a decision, not a default.'
        )
        return winner

    def _stamp_winner(self, winner, rows, window, knobs, trial_count) -> None:
        """Re-stamp the winner's ``metadata['sweep']`` with the whole-campaign evidence."""
        version_id = winner.get('model_version_id')
        if not version_id:
            self.stdout.write(self.style.WARNING(
                f'winner {winner["name"]} has no recorded model_version_id (it was skipped from '
                'a row written before this stamp existed); its sweep metadata is per-trial only.'
            ))
            return
        from apps.prediction.models import ModelVersion

        try:
            row = ModelVersion.objects.get(pk=version_id)
        except ModelVersion.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f'ModelVersion {version_id} for winner {winner["name"]} is gone; nothing to stamp.'
            ))
            return
        metadata = dict(row.metadata or {})
        if 'pooled_holdout' in metadata:
            # Unreachable while sweep= is passed, and worth failing loudly if that ever changes:
            # the key is what makes an ensemble weight this member.
            raise CommandError(
                f'ModelVersion {row.version} carries metadata["pooled_holdout"] despite being '
                'sweep-registered; the contamination discipline has been broken.'
            )
        sweep = dict(metadata.get('sweep') or {})
        sweep.update({
            'selected': True,
            'selected_config': winner['combo'],
            'per_trial_ic': [
                {'trial': row_['name'], 'mean_rank_ic': row_['metrics'].get('mean_rank_ic'),
                 'folds_with_positive_ic': row_['metrics'].get('folds_with_positive_ic'),
                 'folds_trained': row_['metrics'].get('folds_trained'),
                 'fold_ics': row_['fold_ics']}
                for row_ in rows
            ],
            'trials_in_grid': trial_count,
            'tuning_window': window,
            'protocol': knobs,
            'deployment_note': (
                f'Selected by a {trial_count}-trial sweep on the tuning window '
                f'{window["tuning_start"]}..{window["tuning_end"]} only; registered inactive. '
                'The rank ICs here are walk-forward evidence, not a holdout result, and no '
                'portfolio was simulated for this row. Activate only after the Phase D gate.'
            ),
        })
        metadata['sweep'] = sweep
        row.metadata = metadata
        row.save(update_fields=['metadata', 'updated_at'])
        self.stdout.write(self.style.SUCCESS(
            f'stamped sweep provenance on ModelVersion {row.version} '
            f'({len(sweep["per_trial_ic"])} trials recorded, is_active={row.is_active})'
        ))

    def _activation_guard(self, active_before: set[str]) -> str | None:
        """The plan's Phase C acceptance test, run by the command itself."""
        active_after = set(_active_lstm_versions())
        if active_after == active_before:
            self.stdout.write(self.style.SUCCESS(
                f'activation guard: OK -- the active ETF_LSTM set is unchanged '
                f'({sorted(active_after) or "(none)"}).'
            ))
            return None
        message = (
            f'activation guard FAILED: active ETF_LSTM rows changed from {sorted(active_before)} '
            f'to {sorted(active_after)}. A sweep must never activate a candidate; production is '
            'serving whichever model that row now points at.'
        )
        self.stdout.write(self.style.ERROR(message))
        return message


def _active_lstm_versions() -> list[str]:
    """Versions currently flagged active for ETF_LSTM, without importing torch."""
    from apps.prediction.models import ModelVersion

    return list(
        ModelVersion.objects.filter(model_type=MODEL_TYPE_LABEL, is_active=True)
        .order_by('version').values_list('version', flat=True)
    )


def _row_result(row: ETFRotationBacktest, combo: dict[str, Any]) -> dict[str, Any]:
    """Sweep table entry rebuilt from an already-COMPLETED ledger row (resumed sweep)."""
    tuning = (row.report or {}).get('tuning_only') or {}
    parameters = row.parameters or {}
    return {
        'name': row.name, 'combo': combo, 'id': row.pk,
        'fingerprint': str(parameters.get('fingerprint') or ''),
        'metrics': {
            'mean_rank_ic': tuning.get('mean_rank_ic'), 'mean_icir': tuning.get('mean_icir'),
            'folds_with_positive_ic': tuning.get('folds_with_positive_ic'),
            'folds_trained': tuning.get('folds_trained'),
        },
        'fold_ics': tuning.get('fold_ics') or [],
        'model_version': parameters.get('model_version'),
        'model_version_id': parameters.get('model_version_id'),
        'ledger': 'skipped',
    }


def _sort_key(row: dict[str, Any]):
    """Mean fold rank IC, then positive-fold count; a trial with neither sorts last."""
    def value(key: str) -> float:
        raw = row['metrics'].get(key)
        return float(raw) if raw is not None and math.isfinite(float(raw)) else -math.inf
    return (value('mean_rank_ic'), value('folds_with_positive_ic'))


def _loss_short(loss: str) -> str:
    return 'bce' if loss == 'bce_top_bucket' else 'pair'


def _num(value: Any, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return 'n/a'
    return f'{float(value):.{digits}f}'
