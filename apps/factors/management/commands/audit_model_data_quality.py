from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max, Min, Q

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.markets.models import Asset, OHLCV


SCORE_FIELDS = (
    'technical_score',
    'technical_reversal_score',
    'fundamental_score',
    'capital_flow_score',
    'sentiment_score',
    'roe_trend_score',
)


class Command(BaseCommand):
    help = 'Audit default/null model-data buckets for a historical date range without modifying data.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Inclusive start date in YYYY-MM-DD format.')
        parser.add_argument('--end-date', required=True, help='Inclusive end date in YYYY-MM-DD format.')
        parser.add_argument('--symbol', default='', help='Optional asset symbol or TuShare ts_code to diagnose.')
        parser.add_argument('--sample-size', type=int, default=5, help='Maximum rows to print for suspicious samples.')

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        sample_size = max(0, int(options.get('sample_size') or 0))

        self.stdout.write(f'Data quality audit: {start_date}..{end_date}')
        self._write_factor_score_audit(start_date, end_date, sample_size)
        self._write_fundamental_audit(start_date, end_date)
        self._write_capital_flow_audit(start_date, end_date)
        self._write_rs_score_audit(start_date, end_date)

        symbol = str(options.get('symbol') or '').strip()
        if symbol:
            self._write_symbol_diagnostic(symbol, start_date, end_date, sample_size)

    def _parse_date(self, value, label):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {label}: {value}. Expected YYYY-MM-DD.') from exc

    def _write_factor_score_audit(self, start_date, end_date, sample_size):
        queryset = FactorScore.objects.filter(
            date__gte=start_date,
            date__lte=end_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        )
        row_count = queryset.count()
        self.stdout.write('')
        self.stdout.write(f'FactorScore rows={row_count} mode={FactorScore.FactorMode.COMPOSITE}')

        for row in queryset.values('metadata__source').annotate(
            rows=Count('id'),
            first_date=Min('date'),
            last_date=Max('date'),
        ).order_by('metadata__source'):
            source = row['metadata__source'] or '<missing>'
            self.stdout.write(
                f'  source={source} rows={row["rows"]} first_date={row["first_date"]} last_date={row["last_date"]}'
            )

        for field in SCORE_FIELDS:
            counts = self._value_bucket_counts(queryset, field)
            self.stdout.write(
                f'  field={field} total={counts["total"]} non_null={counts["non_null"]} '
                f'null={counts["null"]} zero={counts["zero"]} half={counts["half"]}'
            )

        self._write_score_samples(
            queryset,
            field='technical_score',
            value=Decimal('0.5'),
            label='technical_score=0.5 samples',
            sample_size=sample_size,
        )

    def _value_bucket_counts(self, queryset, field):
        return queryset.aggregate(
            total=Count('id'),
            non_null=Count(field),
            null=Count('id', filter=Q(**{f'{field}__isnull': True})),
            zero=Count('id', filter=Q(**{field: Decimal('0')})),
            half=Count('id', filter=Q(**{field: Decimal('0.5')})),
        )

    def _write_score_samples(self, queryset, field, value, label, sample_size):
        if sample_size <= 0:
            return

        samples = list(
            queryset.filter(**{field: value})
            .select_related('asset')
            .order_by('date', 'asset__ts_code')[:sample_size]
        )
        if not samples:
            self.stdout.write(f'  {label}: none')
            return

        self.stdout.write(f'  {label}:')
        for score in samples:
            source = (score.metadata or {}).get('source') or '<missing>'
            self.stdout.write(
                f'    {score.asset.ts_code} date={score.date} source={source} '
                f'technical_score={score.technical_score} technical_reversal_score={score.technical_reversal_score}'
            )

    def _write_fundamental_audit(self, start_date, end_date):
        queryset = FundamentalFactorSnapshot.objects.filter(date__gte=start_date, date__lte=end_date)
        counts = queryset.aggregate(
            rows=Count('id'),
            roe_null=Count('id', filter=Q(roe__isnull=True)),
            roe_qoq_null=Count('id', filter=Q(roe_qoq__isnull=True)),
            report_key_missing=Count('id', filter=Q(metadata__fina_indicator_end_date__isnull=True)),
            report_key_json_null=Count('id', filter=Q(metadata__fina_indicator_end_date=None)),
        )
        self.stdout.write('')
        self.stdout.write(
            f'FundamentalFactorSnapshot rows={counts["rows"]} roe_null={counts["roe_null"]} '
            f'roe_qoq_null={counts["roe_qoq_null"]} '
            f'fina_report_missing={counts["report_key_missing"] + counts["report_key_json_null"]}'
        )

    def _write_capital_flow_audit(self, start_date, end_date):
        queryset = CapitalFlowSnapshot.objects.filter(date__gte=start_date, date__lte=end_date)
        counts = queryset.aggregate(
            rows=Count('id'),
            main_force_null=Count('id', filter=Q(main_force_net_5d__isnull=True)),
            margin_null=Count('id', filter=Q(margin_balance_change_5d__isnull=True)),
        )
        self.stdout.write('')
        self.stdout.write(
            f'CapitalFlowSnapshot rows={counts["rows"]} main_force_net_5d_null={counts["main_force_null"]} '
            f'margin_balance_change_5d_null={counts["margin_null"]}'
        )

    def _write_rs_score_audit(self, start_date, end_date):
        queryset = TechnicalIndicator.objects.filter(
            indicator_type='RS_SCORE',
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
        )
        counts = queryset.aggregate(
            rows=Count('id'),
            half=Count('id', filter=Q(value=Decimal('0.5'))),
            zero=Count('id', filter=Q(value=Decimal('0'))),
        )
        self.stdout.write('')
        self.stdout.write(f'TechnicalIndicator RS_SCORE rows={counts["rows"]} zero={counts["zero"]} half={counts["half"]}')

    def _write_symbol_diagnostic(self, symbol, start_date, end_date, sample_size):
        assets = list(
            Asset.objects.filter(Q(ts_code__iexact=symbol) | Q(symbol__iexact=symbol)).order_by('ts_code')
        )
        self.stdout.write('')
        self.stdout.write(f'Symbol diagnostic requested={symbol} matches={len(assets)}')
        for asset in assets:
            self._write_asset_diagnostic(asset, start_date, end_date, sample_size)

    def _write_asset_diagnostic(self, asset, start_date, end_date, sample_size):
        self.stdout.write(
            f'  asset={asset.ts_code} symbol={asset.symbol} name={asset.name} '
            f'list_date={asset.list_date} listing_status={asset.listing_status}'
        )

        ohlcv_counts = OHLCV.objects.filter(asset=asset, date__gte=start_date, date__lte=end_date).aggregate(
            rows=Count('id'),
            first_date=Min('date'),
            last_date=Max('date'),
        )
        self.stdout.write(
            f'  ohlcv rows={ohlcv_counts["rows"]} first_date={ohlcv_counts["first_date"]} '
            f'last_date={ohlcv_counts["last_date"]}'
        )

        exact_fundamental = FundamentalFactorSnapshot.objects.filter(asset=asset, date=end_date).first()
        latest_fundamental = (
            FundamentalFactorSnapshot.objects.filter(asset=asset, date__lte=end_date).order_by('-date').first()
        )
        next_fundamental = (
            FundamentalFactorSnapshot.objects.filter(asset=asset, date__gt=end_date).order_by('date').first()
        )
        self._write_fundamental_row('fundamental_exact', exact_fundamental)
        self._write_fundamental_row('fundamental_latest_lte_end', latest_fundamental)
        self._write_fundamental_row('fundamental_next_gt_end', next_fundamental)

        exact_score = FactorScore.objects.filter(
            asset=asset,
            date=end_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).first()
        latest_score = FactorScore.objects.filter(
            asset=asset,
            date__lte=end_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).order_by('-date').first()
        self._write_factor_score_row('factor_score_exact', exact_score)
        self._write_factor_score_row('factor_score_latest_lte_end', latest_score)

        if sample_size > 0:
            recent = FundamentalFactorSnapshot.objects.filter(asset=asset, date__lte=end_date).order_by('-date')[:sample_size]
            for row in recent:
                self._write_fundamental_row('fundamental_recent', row)

    def _write_fundamental_row(self, label, row):
        if row is None:
            self.stdout.write(f'  {label}=none')
            return

        metadata = row.metadata or {}
        self.stdout.write(
            f'  {label} date={row.date} pe={row.pe} pb={row.pb} roe={row.roe} roe_qoq={row.roe_qoq} '
            f'fina_indicator_ann_date={metadata.get("fina_indicator_ann_date")} '
            f'fina_indicator_end_date={metadata.get("fina_indicator_end_date")}'
        )

    def _write_factor_score_row(self, label, row):
        if row is None:
            self.stdout.write(f'  {label}=none')
            return

        metadata = row.metadata or {}
        self.stdout.write(
            f'  {label} date={row.date} source={metadata.get("source") or "<missing>"} '
            f'technical_score={row.technical_score} technical_reversal_score={row.technical_reversal_score} '
            f'fundamental_score={row.fundamental_score} capital_flow_score={row.capital_flow_score} '
            f'sentiment_score={row.sentiment_score}'
        )