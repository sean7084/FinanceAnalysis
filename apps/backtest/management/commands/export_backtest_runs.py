import csv
import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.backtest.models import BacktestRun
from apps.prediction.models import ModelVersion
from apps.prediction.models_lightgbm import LightGBMModelArtifact


PARAMETER_KEYS = [
    'prediction_source',
    'candidate_mode',
    'top_n_metric',
    'horizon_days',
    'top_n',
    'max_positions',
    'up_threshold',
    'trade_score_scope',
    'trade_score_threshold',
    'entry_weekdays',
    'holding_period_days',
    'capital_fraction_per_entry',
    'fee_rate',
    'slippage_bps',
    'use_macro_context',
    'enable_stop_target_exit',
]

METRIC_KEYS = [
    'total_return',
    'annualized_return',
    'max_drawdown',
    'sharpe_ratio',
    'win_rate',
    'total_trades',
    'winning_trades',
]

SIGNAL_KEYS = [
    'prediction_source',
    'candidate_mode',
    'top_n_metric',
    'horizon_days',
    'up_probability',
    'flat_probability',
    'down_probability',
    'confidence',
    'predicted_label',
    'trade_score',
    'target_price',
    'stop_loss_price',
    'suggested',
    'model_version_id',
    'model_version',
    'model_artifact_id',
    'generated_on_demand',
    'macro_multiplier',
    'rank_value',
]


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _json_cell(value):
    if value in (None, ''):
        return ''
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _benchmark_summary(report):
    benchmark = report.get('benchmark') if isinstance(report, dict) else None
    if not isinstance(benchmark, dict):
        return {}
    equity_curve = benchmark.get('equity_curve') or []
    return {
        'benchmark_total_return': benchmark.get('total_return'),
        'benchmark_equity_final': equity_curve[-1] if equity_curve else '',
        'benchmark_strategy': benchmark.get('strategy'),
    }


def _write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _cell(row.get(field)) for field in fieldnames})


class Command(BaseCommand):
    help = 'Export detailed BacktestRun configuration and results to CSV files.'

    def add_arguments(self, parser):
        parser.add_argument('--start-id', type=int, default=89)
        parser.add_argument('--end-id', type=int, default=112)
        parser.add_argument('--output-dir', default='reports/backtests_89_112_v0_1_9')
        parser.add_argument('--compare-start-id', type=int, default=101)
        parser.add_argument('--compare-end-id', type=int, default=106)
        parser.add_argument('--compare-offset', type=int, default=6)

    def handle(self, *args, **options):
        start_id = int(options['start_id'])
        end_id = int(options['end_id'])
        if end_id < start_id:
            raise CommandError('end-id must be greater than or equal to start-id.')

        output_dir = Path(options['output_dir'])
        runs = list(
            BacktestRun.objects.filter(id__gte=start_id, id__lte=end_id)
            .select_related('user')
            .prefetch_related('trades__asset')
            .order_by('id')
        )
        if not runs:
            raise CommandError(f'No BacktestRun rows found for id range {start_id}..{end_id}.')

        requested_ids = list(range(start_id, end_id + 1))

        self._export_run_summary(output_dir, runs, requested_ids)
        self._export_run_config_results(output_dir, runs, requested_ids)
        self._export_trades(output_dir, runs)
        self._export_macro_context(output_dir, runs)
        self._export_model_references(output_dir, runs)
        self._export_comparison(output_dir, runs, options)

        self.stdout.write(self.style.SUCCESS(
            f'Exported {len(runs)} existing backtest runs from requested range {start_id}..{end_id} to {output_dir}'
        ))

    def _missing_run_row(self, run_id):
        return {
            'run_id': run_id,
            'name': '',
            'strategy_type': '',
            'status': 'MISSING',
            'error_message': 'No BacktestRun row exists for this ID.',
        }

    def _base_run_row(self, run):
        report = run.report or {}
        row = {
            'run_id': run.id,
            'name': run.name,
            'user_id': run.user_id,
            'user_email': getattr(run.user, 'email', '') if run.user else '',
            'strategy_type': run.strategy_type,
            'status': run.status,
            'start_date': run.start_date,
            'end_date': run.end_date,
            'initial_capital': run.initial_capital,
            'cash': run.cash,
            'final_value': run.final_value,
            'num_trading_days': report.get('num_trading_days') if isinstance(report, dict) else '',
            'trade_row_count': run.trades.count(),
            'created_at': run.created_at,
            'updated_at': run.updated_at,
            'started_at': run.started_at,
            'completed_at': run.completed_at,
            'error_message': run.error_message,
        }
        for metric_key in METRIC_KEYS:
            row[metric_key] = getattr(run, metric_key)
        row.update(_benchmark_summary(report))
        return row

    def _export_run_summary(self, output_dir, runs, requested_ids):
        fields = [
            'run_id', 'name', 'user_id', 'user_email', 'strategy_type', 'status',
            'start_date', 'end_date', 'initial_capital', 'cash', 'final_value',
            *METRIC_KEYS,
            'num_trading_days', 'trade_row_count',
            'benchmark_total_return', 'benchmark_equity_final', 'benchmark_strategy',
            'created_at', 'updated_at', 'started_at', 'completed_at', 'error_message',
        ]
        runs_by_id = {run.id: run for run in runs}
        rows = [
            self._base_run_row(runs_by_id[run_id]) if run_id in runs_by_id else self._missing_run_row(run_id)
            for run_id in requested_ids
        ]
        _write_csv(output_dir / 'run_summary.csv', fields, rows)

    def _export_run_config_results(self, output_dir, runs, requested_ids):
        fields = [
            'run_id', 'name', 'status', 'strategy_type', 'start_date', 'end_date',
            'initial_capital', 'cash', 'final_value', *METRIC_KEYS,
            *PARAMETER_KEYS,
            'num_trading_days', 'benchmark_total_return', 'benchmark_equity_final',
            'parameters_json', 'report_keys', 'created_at', 'completed_at', 'error_message',
        ]
        rows = []
        runs_by_id = {run.id: run for run in runs}
        for run_id in requested_ids:
            run = runs_by_id.get(run_id)
            if run is None:
                rows.append(self._missing_run_row(run_id))
                continue
            parameters = run.parameters or {}
            report = run.report or {}
            row = self._base_run_row(run)
            for parameter_key in PARAMETER_KEYS:
                row[parameter_key] = parameters.get(parameter_key)
            row['parameters_json'] = _json_cell(parameters)
            row['report_keys'] = sorted(report.keys()) if isinstance(report, dict) else []
            rows.append(row)
        _write_csv(output_dir / 'run_config_results.csv', fields, rows)

    def _export_trades(self, output_dir, runs):
        fields = [
            'run_id', 'run_name', 'trade_id', 'asset_id', 'asset_symbol', 'asset_ts_code', 'asset_name',
            'trade_date', 'side', 'quantity', 'price', 'fee', 'slippage', 'amount', 'pnl',
            *SIGNAL_KEYS,
            'exit_reason', 'metadata_json', 'signal_payload_json', 'created_at',
        ]
        rows = []
        for run in runs:
            for trade in run.trades.all().order_by('trade_date', 'id'):
                signal_payload = trade.signal_payload or {}
                metadata = trade.metadata or {}
                row = {
                    'run_id': run.id,
                    'run_name': run.name,
                    'trade_id': trade.id,
                    'asset_id': trade.asset_id,
                    'asset_symbol': trade.asset.symbol,
                    'asset_ts_code': trade.asset.ts_code,
                    'asset_name': trade.asset.name,
                    'trade_date': trade.trade_date,
                    'side': trade.side,
                    'quantity': trade.quantity,
                    'price': trade.price,
                    'fee': trade.fee,
                    'slippage': trade.slippage,
                    'amount': trade.amount,
                    'pnl': trade.pnl,
                    'exit_reason': metadata.get('exit_reason') or signal_payload.get('exit_reason'),
                    'metadata_json': _json_cell(metadata),
                    'signal_payload_json': _json_cell(signal_payload),
                    'created_at': trade.created_at,
                }
                for signal_key in SIGNAL_KEYS:
                    row[signal_key] = signal_payload.get(signal_key)
                rows.append(row)
        _write_csv(output_dir / 'trades.csv', fields, rows)

    def _export_macro_context(self, output_dir, runs):
        rows = []
        extra_keys = set()
        for run in runs:
            report = run.report or {}
            monthly_rows = report.get('macro_context_monthly') if isinstance(report, dict) else []
            if not isinstance(monthly_rows, list):
                continue
            for index, monthly_row in enumerate(monthly_rows, start=1):
                if isinstance(monthly_row, dict):
                    extra_keys.update(monthly_row.keys())
                    row = {'run_id': run.id, 'run_name': run.name, 'row_number': index, **monthly_row}
                else:
                    row = {'run_id': run.id, 'run_name': run.name, 'row_number': index, 'value': monthly_row}
                rows.append(row)
        fields = ['run_id', 'run_name', 'row_number'] + sorted(extra_keys | {'value'})
        _write_csv(output_dir / 'macro_context_monthly.csv', fields, rows)

    def _export_model_references(self, output_dir, runs):
        fields = [
            'source', 'prediction_source', 'reference_type', 'reference_id', 'version', 'horizon_days',
            'status', 'is_active', 'training_window_start', 'training_window_end', 'trained_at',
            'metrics_json', 'feature_count', 'run_ids',
        ]
        rows = []
        version_refs = {}
        artifact_refs = {}
        for run in runs:
            for trade in run.trades.all():
                signal_payload = trade.signal_payload or {}
                if signal_payload.get('model_version_id'):
                    version_refs.setdefault(int(signal_payload['model_version_id']), set()).add(run.id)
                if signal_payload.get('model_artifact_id'):
                    artifact_refs.setdefault(int(signal_payload['model_artifact_id']), set()).add(run.id)

        for model_version in ModelVersion.objects.filter(id__in=version_refs.keys()).order_by('model_type', 'version'):
            rows.append({
                'source': 'trade_signal_payload',
                'prediction_source': model_version.model_type.lower(),
                'reference_type': 'ModelVersion',
                'reference_id': model_version.id,
                'version': model_version.version,
                'status': model_version.status,
                'is_active': model_version.is_active,
                'training_window_start': model_version.training_window_start,
                'training_window_end': model_version.training_window_end,
                'trained_at': model_version.trained_at,
                'metrics_json': _json_cell(model_version.metrics),
                'feature_count': len(model_version.feature_schema or []),
                'run_ids': sorted(version_refs.get(model_version.id, [])),
            })

        for artifact in LightGBMModelArtifact.objects.filter(id__in=artifact_refs.keys()).order_by('horizon_days', 'version'):
            rows.append({
                'source': 'trade_signal_payload',
                'prediction_source': 'lightgbm',
                'reference_type': 'LightGBMModelArtifact',
                'reference_id': artifact.id,
                'version': artifact.version,
                'horizon_days': artifact.horizon_days,
                'status': artifact.status,
                'is_active': artifact.is_active,
                'training_window_start': artifact.training_window_start,
                'training_window_end': artifact.training_window_end,
                'trained_at': artifact.trained_at,
                'metrics_json': _json_cell(artifact.metrics_json),
                'feature_count': len(artifact.feature_names or []),
                'run_ids': sorted(artifact_refs.get(artifact.id, [])),
            })

        active_versions = ModelVersion.objects.filter(is_active=True).order_by('model_type', 'version')
        for model_version in active_versions:
            rows.append({
                'source': 'active_registry',
                'prediction_source': model_version.model_type.lower(),
                'reference_type': 'ModelVersion',
                'reference_id': model_version.id,
                'version': model_version.version,
                'status': model_version.status,
                'is_active': model_version.is_active,
                'training_window_start': model_version.training_window_start,
                'training_window_end': model_version.training_window_end,
                'trained_at': model_version.trained_at,
                'metrics_json': _json_cell(model_version.metrics),
                'feature_count': len(model_version.feature_schema or []),
            })

        active_artifacts = LightGBMModelArtifact.objects.filter(is_active=True).order_by('horizon_days', 'version')
        for artifact in active_artifacts:
            rows.append({
                'source': 'active_registry',
                'prediction_source': 'lightgbm',
                'reference_type': 'LightGBMModelArtifact',
                'reference_id': artifact.id,
                'version': artifact.version,
                'horizon_days': artifact.horizon_days,
                'status': artifact.status,
                'is_active': artifact.is_active,
                'training_window_start': artifact.training_window_start,
                'training_window_end': artifact.training_window_end,
                'trained_at': artifact.trained_at,
                'metrics_json': _json_cell(artifact.metrics_json),
                'feature_count': len(artifact.feature_names or []),
            })
        _write_csv(output_dir / 'model_references.csv', fields, rows)

    def _export_comparison(self, output_dir, runs, options):
        compare_start_id = int(options['compare_start_id'])
        compare_end_id = int(options['compare_end_id'])
        compare_offset = int(options['compare_offset'])
        runs_by_id = {run.id: run for run in runs}
        fields = [
            'left_run_id', 'right_run_id', 'left_name', 'right_name', 'left_status', 'right_status',
            *[f'left_{metric_key}' for metric_key in METRIC_KEYS],
            *[f'right_{metric_key}' for metric_key in METRIC_KEYS],
            *[f'delta_{metric_key}' for metric_key in METRIC_KEYS],
        ]
        rows = []
        for left_run_id in range(compare_start_id, compare_end_id + 1):
            right_run_id = left_run_id + compare_offset
            left_run = runs_by_id.get(left_run_id)
            right_run = runs_by_id.get(right_run_id)
            row = {
                'left_run_id': left_run_id,
                'right_run_id': right_run_id,
                'left_name': left_run.name if left_run else '',
                'right_name': right_run.name if right_run else '',
                'left_status': left_run.status if left_run else 'MISSING',
                'right_status': right_run.status if right_run else 'MISSING',
            }
            for metric_key in METRIC_KEYS:
                left_value = getattr(left_run, metric_key) if left_run else None
                right_value = getattr(right_run, metric_key) if right_run else None
                row[f'left_{metric_key}'] = left_value
                row[f'right_{metric_key}'] = right_value
                if isinstance(left_value, Decimal) and isinstance(right_value, Decimal):
                    row[f'delta_{metric_key}'] = right_value - left_value
                elif isinstance(left_value, int) and isinstance(right_value, int):
                    row[f'delta_{metric_key}'] = right_value - left_value
                else:
                    row[f'delta_{metric_key}'] = ''
            rows.append(row)
        _write_csv(output_dir / 'comparison_101_106_vs_107_112.csv', fields, rows)