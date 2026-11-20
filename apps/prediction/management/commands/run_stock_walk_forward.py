"""Run one stock walk-forward cell (H2 / plan D7-D11).

Orchestrates the frozen-panel fold campaign for a single (arm, panel_start) across
the declared horizons: preregistration gating, board-level matrix build (cached),
arm row-mask, per-fold pure fit, out-of-sample metrics, top-N simulator net value,
and one append-only ledger row per horizon carrying the prereg digest. Fold
artifacts are persisted is_active=False (never activated).

The campaign run is a research write gated on the data runs (DR1-7) and the signed
preregistration (P1, owner O3/O4/O5). This command refuses any cell not declared in
the manifest and never adds panel starts after results.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.core.date_contract import STOCK_TRAINING_START
from apps.markets.benchmarking import DEFAULT_PIT_INDEX_CODES
from apps.markets.models import OHLCV, Asset, AssetSuspension
from apps.markets.st_history import st_asset_ids_by_dates
from apps.prediction import tasks_lightgbm as tl
from apps.prediction.evidence import record_stock_measurement
from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.prereg import PreregistrationError, load_preregistration
from apps.prediction.simulator import simulate_topn_returns
from apps.prediction.stock_matrix_provider import DBMatrixProvider
from apps.prediction.walk_forward_runner import WalkForwardCell, run_cell


class Command(BaseCommand):
    help = 'Run one stock walk-forward (arm, panel_start) cell over the frozen panel (prereg-gated).'

    def add_arguments(self, parser):
        parser.add_argument('--preregistration', required=True, help='Path to the signed preregistration markdown.')
        parser.add_argument('--arm', required=True, help='Universe arm: board_only / board_st / cap.')
        parser.add_argument('--panel-start', required=True, help='Declared panel start (YYYY-MM-DD).')
        parser.add_argument('--panel-end', required=True, help='Frozen panel end; must equal the manifest.')
        parser.add_argument('--horizons', default='3,7,30', help='Comma-separated horizons (default 3,7,30).')
        parser.add_argument('--index-codes', default=None, help='Comma-separated PIT index codes (default serving).')
        parser.add_argument('--execution-model', default='next_day_open', help='next_day_open / same_day_close.')
        parser.add_argument('--checkpoint-file', default=None, help='JSON checkpoint for --resume.')
        parser.add_argument('--resume', action='store_true', help='Skip folds already in the checkpoint.')

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def handle(self, *args, **options):
        try:
            manifest, digest = load_preregistration(options['preregistration'])
        except PreregistrationError as exc:
            raise CommandError(str(exc)) from exc

        panel_start = self._parse_date(options['panel_start'], 'panel-start')
        panel_end = self._parse_date(options['panel_end'], 'panel-end')
        if str(panel_end) != str(manifest['panel_end']):
            raise CommandError(
                f"panel-end {panel_end} does not match the preregistration ({manifest['panel_end']}); "
                'the frozen panel cannot be changed after registration.'
            )
        horizons = tuple(int(h.strip()) for h in options['horizons'].split(',') if h.strip())
        index_codes = (
            tuple(c.strip() for c in options['index_codes'].split(',') if c.strip())
            if options['index_codes'] else tuple(DEFAULT_PIT_INDEX_CODES)
        )
        feature_names = tuple(manifest.get('feature_names') or tl.TRAINING_BASE_FEATURES_V3)
        simulator_params = dict(manifest.get('simulator_params') or {})

        cell = WalkForwardCell(
            arm=options['arm'], panel_start=panel_start, panel_end=panel_end,
            horizons=horizons, index_codes=index_codes, feature_names=feature_names,
            execution_model=options['execution_model'], simulator_params=simulator_params,
            schema_version=manifest.get('schema_version', 'v3'),
            fold_plan=manifest.get('fold_plan', 'expanding-7'),
        )

        trade_dates = list(
            OHLCV.objects.filter(date__gte=STOCK_TRAINING_START, date__lte=panel_end)
            .values_list('date', flat=True).distinct().order_by('date')
        )
        provider = DBMatrixProvider(
            panel_start=panel_start, panel_end=panel_end, index_codes=index_codes, horizons=horizons,
        )
        ts_code_map = dict(Asset.objects.values_list('id', 'ts_code'))
        suspension_set = set(
            AssetSuspension.objects.filter(trade_date__gte=panel_start, trade_date__lte=panel_end)
            .values_list('asset_id', 'trade_date')
        )
        st_by_date = st_asset_ids_by_dates(trade_dates)

        def predict_fn(fit, valid):
            X = fit['scaler'].transform(valid[list(feature_names)].values)
            return fit['calibrator'].predict_proba(X)

        def simulate_fn(predictions, prices, **kwargs):
            return simulate_topn_returns(
                predictions, prices, ts_code_map=ts_code_map,
                suspension_set=suspension_set, st_by_date=st_by_date, **kwargs,
            )

        def persist_fn(fit, horizon, tag):
            model_dict = dict(fit)
            model_dict['feature_names'] = list(feature_names)
            tl._save_model_artifacts(model_dict, horizon, tag, extra_metadata={
                'fold_artifact': True, 'arm': cell.arm, 'universe_version': 'v1',
                'pit_index_codes': list(index_codes),
            })
            LightGBMModelArtifact.objects.update_or_create(
                horizon_days=horizon, version=tag,
                defaults={
                    'is_active': False, 'status': LightGBMModelArtifact.Status.READY,
                    'feature_names': list(feature_names),
                    'metrics_json': fit.get('holdout_metrics') or {},
                    'metadata': {'fold_artifact': True, 'arm': cell.arm, 'preregistration': digest},
                },
            )

        checkpoint = self._load_checkpoint(options) if options['resume'] else set()
        summary = run_cell(
            cell, trade_dates=trade_dates, provider=provider,
            fit_fn=tl._fit_lightgbm_horizon, predict_fn=predict_fn, simulate_fn=simulate_fn,
            record_fn=record_stock_measurement, holdout_metrics_fn=tl._holdout_metrics,
            persist_fn=persist_fn, preregistration=digest, manifest=manifest, checkpoint=checkpoint,
        )
        if options['checkpoint_file']:
            self._save_checkpoint(options['checkpoint_file'], checkpoint)
        self.stdout.write(self.style.SUCCESS(
            f"arm={cell.arm} panel_start={panel_start} horizons={list(horizons)} digest={digest} "
            f"measurements={[h['measurement_id'] for h in summary['horizons'].values()]}"
        ))

    def _load_checkpoint(self, options):
        import json
        path = options['checkpoint_file']
        if not path:
            raise CommandError('--resume requires --checkpoint-file.')
        try:
            with open(path, encoding='utf-8') as handle:
                raw = json.load(handle)
        except OSError:
            return set()
        return {(int(h), int(i)) for h, i in raw}

    def _save_checkpoint(self, path, checkpoint):
        import json
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(sorted([list(k) for k in checkpoint]), handle)