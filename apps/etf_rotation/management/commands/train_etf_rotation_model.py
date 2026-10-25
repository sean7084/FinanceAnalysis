"""Train, plan, or report the ETF rotation models with walk-forward validation.

``--dry-run`` and ``--report`` are read-only by construction: the dry run goes through
``plan_walk_forward``, which issues SELECTs only, creates no artifact directory and touches
no ``ModelVersion`` row. Use it to see whether the panel is worth a retrain before paying
for one -- the per-fold purged-date counts and query-group sizes are the numbers that say
so.

Examples::

    python manage.py train_etf_rotation_model --dry-run --folds 6
    python manage.py train_etf_rotation_model --type lightgbm --objective lambdarank
    python manage.py train_etf_rotation_model --type all --start 2019-01-01 --end 2026-06-30
    python manage.py train_etf_rotation_model --report
"""

from __future__ import annotations

import importlib
import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.etf_rotation.train_lightgbm import (
    DEFAULT_OBJECTIVE,
    OBJECTIVES,
    plan_walk_forward,
    register_ensemble_version,
    registered_versions,
    train_walk_forward,
)


def _parse_date(label: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'--{label} must be ISO format YYYY-MM-DD, got {value!r}.') from exc


class Command(BaseCommand):
    help = 'Walk-forward training and reporting for the ETF rotation models.'

    def add_arguments(self, parser):
        parser.add_argument('--type', default='lightgbm', choices=['lightgbm', 'lstm', 'ensemble', 'all'],
                            help='Which ETF model to act on (default lightgbm).')
        parser.add_argument('--start', help='First snapshot date to consider (ISO). Default: earliest eligible row.')
        parser.add_argument('--end', help='Last snapshot date to consider (ISO). Default: latest eligible row.')
        parser.add_argument('--folds', type=int, default=6,
                            help='Number of expanding-window validation steps (default 6).')
        parser.add_argument('--objective', default=DEFAULT_OBJECTIVE, choices=list(OBJECTIVES),
                            help='lambdarank on label_rank_pct grades (default), binary on label_top_bucket, '
                                 'or regression on label_rank_pct.')
        parser.add_argument('--boost-rounds', type=int, help='Fixed lgb.train round count; no early stopping.')
        parser.add_argument('--horizon', type=int,
                            help='Label horizon in trading sessions. Default: settings.ETF_LABEL_HORIZON_DAYS (20).')
        parser.add_argument('--min-train-days', type=int,
                            help='Minimum dates in the first training window. Default: settings.ETF_MIN_TRAIN_DAYS (250).')
        parser.add_argument('--version-tag', default='', help='Suffix appended to the ModelVersion key.')
        parser.add_argument('--no-activate', action='store_true',
                            help='Register the trained row inactive and leave the serving model alone. '
                                 'Required for a post-release retrain: this command activates by default.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Build the frames and fold plan, print sample/group counts, train nothing, write nothing.')
        parser.add_argument('--report', action='store_true',
                            help='Print registered ETF model versions with their holdout metrics and exit.')

    def handle(self, *args, **options):
        if options['report']:
            self._render_report()
            return

        start = _parse_date('start', options['start']) if options.get('start') else None
        end = _parse_date('end', options['end']) if options.get('end') else None
        common = {
            'start_date': start,
            'end_date': end,
            'n_folds': options['folds'],
            'objective': options['objective'],
        }
        if options['horizon'] is not None:
            common['horizon_days'] = options['horizon']
        if options['min_train_days'] is not None:
            common['min_train_days'] = options['min_train_days']

        if options['dry_run']:
            self._run_dry(common, options['type'], options['version_tag'])
            return

        targets = ['lightgbm', 'lstm', 'ensemble'] if options['type'] == 'all' else [options['type']]
        activate = not options['no_activate']
        failures = []
        for target in targets:
            if target == 'ensemble':
                result = register_ensemble_version(
                    version_tag=options['version_tag'], activate=activate,
                )
                self._render_ensemble(result)
                if result['status'] != 'success':
                    failures.append(f'ensemble: {result.get("reason", result["status"])}')
                continue
            train_kwargs = dict(common)
            # Explicit in both directions: the trainer's default is ``activate=True``, and a
            # post-release retrain that silently took over serving would be the exact mistake
            # the activation gate exists to prevent.
            train_kwargs['activate'] = activate
            if options['boost_rounds'] is not None:
                train_kwargs['num_boost_round'] = options['boost_rounds']
            if options['version_tag']:
                train_kwargs['version_tag'] = options['version_tag']
            if target == 'lstm':
                train_kwargs['model_type_label'] = 'ETF_LSTM'
                result = self._train_lstm(train_kwargs)
            else:
                result = train_walk_forward(model_type_label='ETF_LIGHTGBM', **train_kwargs)
            self._render_training(target, result, activate=activate)
            if result.get('status') != 'success':
                failures.append(f'{target}: {result.get("reason", result.get("status"))}')
        if failures:
            raise CommandError('Training did not produce a registered model: ' + '; '.join(failures))

    # -- dry run --------------------------------------------------------------
    def _run_dry(self, common, model_type, version_tag=''):
        """Plan only. Nothing here writes a row, a directory, or an artifact."""
        if model_type == 'ensemble':
            self.stdout.write('ensemble has no walk-forward plan to dry-run; it weights registered '
                              'members, so run --report instead.')
            return
        if model_type == 'lstm':
            targets = ['lstm']
        elif model_type == 'all':
            targets = ['lightgbm', 'lstm']
        else:
            targets = ['lightgbm']
        for target in targets:
            if target == 'lstm':
                self._plan_lstm(common)
                continue
            try:
                plan = plan_walk_forward(version_tag=version_tag, **common)
            except (RuntimeError, ValueError) as exc:
                raise CommandError(f'Cannot plan ETF LightGBM: {exc}') from exc
            self._render_plan('lightgbm', plan)

    def _plan_lstm(self, common):
        planner = self._lstm_callable('plan_walk_forward')
        if planner is None:
            self.stdout.write(self.style.WARNING(
                'train_lstm exposes no plan_walk_forward; skipping the LSTM dry run '
                '(--dry-run never calls train_walk_forward, so nothing was trained).'
            ))
            return
        self._render_plan('lstm', planner(**common))

    # -- collaborators --------------------------------------------------------
    @staticmethod
    def _lstm_callable(name):
        """Resolve ``apps.etf_rotation.train_lstm.<name>``, or None if absent.

        The LSTM module is authored concurrently against the same signature as
        ``train_walk_forward``; importing it lazily keeps this command usable on a branch
        where it does not exist yet.
        """
        try:
            module = importlib.import_module('apps.etf_rotation.train_lstm')
        except ImportError:
            return None
        return getattr(module, name, None)

    def _train_lstm(self, kwargs):
        trainer = self._lstm_callable('train_walk_forward')
        if trainer is None:
            return {'status': 'unavailable',
                    'reason': 'apps.etf_rotation.train_lstm.train_walk_forward is not importable yet.'}
        try:
            return trainer(**kwargs)
        except (RuntimeError, ValueError) as exc:
            return {'status': 'failed', 'reason': str(exc)}

    # -- rendering ------------------------------------------------------------
    def _render_plan(self, target, plan):
        self.stdout.write(f'--- {target} walk-forward plan ---')
        if plan.get('status') != 'ok':
            self.stdout.write(self.style.WARNING(
                f"{plan.get('status')}: {plan.get('reason', 'no plan available')}"
            ))
        self.stdout.write(
            f"window={plan.get('start_date')}..{plan.get('end_date')} "
            f"panel={plan.get('panel_start')}..{plan.get('panel_end')} "
            f"({plan.get('panel_dates')} dates) horizon={plan.get('horizon_days')} "
            f"objective={plan.get('objective')} min_train_days={plan.get('min_train_days')}"
        )
        self.stdout.write(
            f"features={plan.get('feature_count')} schema={plan.get('feature_schema_version')} "
            f"lightgbm_available={plan.get('lightgbm_available')}"
        )
        counts = plan.get('row_counts') or {}
        self.stdout.write(
            f"rows: in={counts.get('input_rows')} out={counts.get('output_rows')} "
            f"wrong_horizon={counts.get('wrong_horizon')} missing_fwd={counts.get('missing_fwd_return')} "
            f"missing_label={counts.get('missing_label')} thin_dates={counts.get('thin_dates_dropped')} "
            f"dupes={counts.get('duplicate_keys')}"
        )
        pool = plan.get('pool') or {}
        self.stdout.write(
            f"pool: etfs={pool.get('unique_etfs')} mean/date={pool.get('pool_mean_per_date')} "
            f"min/date={pool.get('pool_min_per_date')} max/date={pool.get('pool_max_per_date')}"
        )
        self.stdout.write(
            f"folds requested={plan.get('n_folds_requested')} built={plan.get('n_folds_built')}"
        )
        for fold in plan.get('folds') or []:
            self.stdout.write(
                "  fold {fold_index}: train {train_start}..{train_end} "
                "(kept {train_dates_kept} dates, purged {train_dates_dropped}, "
                "{train_rows} rows, {train_groups} groups min/max {train_group_min}/{train_group_max}) "
                "| valid {valid_start}..{valid_end} ({valid_rows} rows, {valid_dates} dates)".format(**fold)
            )
        if plan.get('would_write'):
            self.stdout.write(f"dry-run: would write {plan['would_write']}")

    def _render_training(self, target, result, activate=True):
        status = result.get('status')
        if status != 'success':
            self.stdout.write(self.style.WARNING(f'{target}: {status} - {result.get("reason", "")}'))
            for fold in result.get('folds') or []:
                self.stdout.write(f"  fold {fold.get('fold_index')}: {fold.get('status')} "
                                  f"train_rows={fold.get('train_rows')} valid_rows={fold.get('valid_rows')}")
            return
        metrics = result['metrics']
        self.stdout.write(self.style.SUCCESS(
            f"{target}: registered {result['model_version']} (artifact {result['artifact_path']})"
        ))
        self.stdout.write(
            'activation: '
            + ('ACTIVE (this row now serves signals)' if activate else 'inactive (--no-activate)')
        )
        if result.get('device'):
            self.stdout.write(
                f"train device: {result['device']} "
                f"(torch.cuda.is_available={result.get('cuda_available')})"
            )
        self.stdout.write(
            f"holdout pooled: rank_ic={_fmt(metrics.get('rank_ic'))} icir={_fmt(metrics.get('icir'))} "
            f"top{metrics.get('top_n', 5)}_hit={_fmt(metrics.get('top_n_hit_rate'))} "
            f"ndcg@5={_fmt(metrics.get('ndcg_at_5'))} "
            f"ic_dates={metrics.get('ic_dates')} valid_rows={metrics.get('valid_rows')}"
        )
        aggregate = result['aggregate']
        self.stdout.write(
            f"folds trained={aggregate['folds_trained']} skipped={aggregate['folds_skipped']} "
            f"positive_ic={aggregate['folds_with_positive_ic']} "
            f"mean_rank_ic={_fmt(aggregate['mean_rank_ic'])} "
            f"purged_dates_total={aggregate['total_purged_train_dates']}"
        )
        for fold in result['folds']:
            fold_metrics = fold.get('metrics') or {}
            self.stdout.write(
                f"  fold {fold['fold_index']}: {fold.get('status')} "
                f"train {fold['train_start']}..{fold['train_end']} "
                f"(rows={fold.get('train_rows')} purged={fold['purged_train_dates']} "
                f"groups={fold.get('train_groups')}) "
                f"valid {fold['valid_start']}..{fold['valid_end']} (rows={fold.get('valid_rows')}) "
                f"ic={_fmt(fold_metrics.get('rank_ic'))} icir={_fmt(fold_metrics.get('icir'))} "
                f"hit={_fmt(fold_metrics.get('top_n_hit_rate'))} ndcg={_fmt(fold_metrics.get('ndcg_at_5'))}"
            )

    def _render_ensemble(self, result):
        if result.get('status') != 'success':
            self.stdout.write(self.style.WARNING(
                f"ensemble: {result.get('status')} - {result.get('reason', '')}"
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"ensemble: registered {result['model_version']} "
            f"(basis={result['weight_basis']}, no artifact by design)"
        ))
        for label, weight in sorted(result['weights'].items()):
            member = result['members'].get(label) or {}
            self.stdout.write(f"  {label}: weight={weight:.4f} version={member.get('version')} "
                              f"rank_ic={_fmt(member.get('rank_ic'))}")
        self.stdout.write(
            "  engine-keyed copy: " + json.dumps(result['engine_weights'], sort_keys=True)
            + ' (rotation_engine._rescale_ensemble_weights expects these names)'
        )

    def _render_report(self):
        rows = registered_versions()
        if not rows:
            self.stdout.write(self.style.WARNING(
                'No ETF ModelVersion rows registered yet. Run '
                '`manage.py train_etf_rotation_model --type lightgbm` first.'
            ))
            return
        for row in rows:
            flag = 'ACTIVE' if row['is_active'] else 'inactive'
            self.stdout.write(
                f"{row['model_type']} {row['version']} [{flag}, {row['status']}] "
                f"trained_at={row['trained_at']} window={row['training_window']}"
            )
            self.stdout.write(
                f"  objective={row['objective']} horizon={row['horizon_days']} "
                f"folds={row['fold_count']} purged_dates={row['total_purged_train_dates']} "
                f"artifact={row['artifact_path'] or '(none)'}"
            )
            if row['holdout']:
                self.stdout.write('  holdout: ' + ', '.join(
                    f'{key}={_fmt(value)}' for key, value in row['holdout'].items()
                ))
            if row['weights']:
                self.stdout.write(f"  weights: {json.dumps(row['weights'], sort_keys=True)}")
                if row['engine_weights']:
                    self.stdout.write(
                        '  weights (engine keys): '
                        f"{json.dumps(row['engine_weights'], sort_keys=True)}"
                    )


def _fmt(value) -> str:
    if value is None:
        return 'n/a'
    try:
        return f'{float(value):.4f}'
    except (TypeError, ValueError):
        return str(value)
