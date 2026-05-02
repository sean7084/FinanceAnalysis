import json
from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.backtest.models import BacktestRun, BacktestTrade
from apps.core.date_floor import get_historical_data_floor
from apps.factors.models import (
    AssetMarginDetailSnapshot,
    AssetMoneyFlowSnapshot,
    CapitalFlowSnapshot,
    FactorScore,
    FundamentalFactorSnapshot,
)
from apps.macro.models import EventImpactStat, MacroSnapshot, MarketContext
from apps.markets.models import BenchmarkIndexDaily, IndexMembership, OHLCV
from apps.prediction.models import PredictionResult
from apps.prediction.models_lightgbm import EnsembleWeightSnapshot, LightGBMPrediction
from apps.sentiment.models import ConceptHeat, NewsArticle, SentimentScore


def _parse_date(value, label):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {label}: {value}. Expected YYYY-MM-DD.') from exc


def _start_of_day(value):
    cutoff = datetime.combine(value, time.min)
    if timezone.is_naive(cutoff):
        return timezone.make_aware(cutoff, timezone.get_current_timezone())
    return cutoff


class Command(BaseCommand):
    help = 'Dry-run or delete historical database rows dated before the configured floor date.'

    def add_arguments(self, parser):
        parser.add_argument('--before-date', default=get_historical_data_floor().isoformat())
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Actually delete rows. Without this flag, the command only reports candidate counts.',
        )

    def _querysets(self, before_date):
        before_dt = _start_of_day(before_date)
        return [
            ('sentiment_scores', SentimentScore.objects.filter(date__lt=before_date)),
            ('concept_heat', ConceptHeat.objects.filter(date__lt=before_date)),
            ('news_articles', NewsArticle.objects.filter(published_at__lt=before_dt)),
            ('signal_events', SignalEvent.objects.filter(timestamp__lt=before_dt)),
            ('technical_indicators', TechnicalIndicator.objects.filter(timestamp__lt=before_dt)),
            ('factor_scores', FactorScore.objects.filter(date__lt=before_date)),
            ('capital_flow_snapshots', CapitalFlowSnapshot.objects.filter(date__lt=before_date)),
            ('asset_margin_detail_snapshots', AssetMarginDetailSnapshot.objects.filter(date__lt=before_date)),
            ('asset_money_flow_snapshots', AssetMoneyFlowSnapshot.objects.filter(date__lt=before_date)),
            ('fundamental_factor_snapshots', FundamentalFactorSnapshot.objects.filter(date__lt=before_date)),
            ('prediction_results', PredictionResult.objects.filter(date__lt=before_date)),
            ('lightgbm_predictions', LightGBMPrediction.objects.filter(date__lt=before_date)),
            ('ensemble_weight_snapshots', EnsembleWeightSnapshot.objects.filter(date__lt=before_date)),
            ('backtest_trades', BacktestTrade.objects.filter(trade_date__lt=before_date)),
            ('backtest_runs', BacktestRun.objects.filter(start_date__lt=before_date)),
            (
                'event_impact_stats',
                EventImpactStat.objects.filter(
                    Q(observations_start__lt=before_date) | Q(observations_end__lt=before_date)
                ),
            ),
            ('market_contexts', MarketContext.objects.filter(starts_at__lt=before_date)),
            ('macro_snapshots', MacroSnapshot.objects.filter(date__lt=before_date)),
            ('index_memberships', IndexMembership.objects.filter(trade_date__lt=before_date)),
            ('benchmark_index_daily', BenchmarkIndexDaily.objects.filter(trade_date__lt=before_date)),
            ('ohlcv', OHLCV.objects.filter(date__lt=before_date)),
        ]

    def handle(self, *args, **options):
        before_date = _parse_date(options['before_date'], 'before-date')
        execute = bool(options['execute'])
        floor_date = get_historical_data_floor()

        results = []
        total_candidate_rows = 0
        total_deleted_rows = 0

        for label, queryset in self._querysets(before_date):
            candidate_rows = queryset.count()
            total_candidate_rows += candidate_rows
            row = {
                'label': label,
                'candidate_rows': candidate_rows,
            }
            if execute and candidate_rows:
                deleted_rows, deleted_breakdown = queryset.delete()
                row['deleted_rows'] = deleted_rows
                row['deleted_breakdown'] = deleted_breakdown
                total_deleted_rows += deleted_rows
            results.append(row)

        payload = {
            'before_date': before_date.isoformat(),
            'configured_floor_date': floor_date.isoformat(),
            'execute': execute,
            'total_candidate_rows': total_candidate_rows,
            'total_deleted_rows': total_deleted_rows,
            'results': results,
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str))