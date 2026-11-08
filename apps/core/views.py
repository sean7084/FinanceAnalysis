"""HTTP views for the shared ``apps.core`` infrastructure app.

Exposes the SPA fallback that renders the Vite-built frontend shell, plus the
read-only ``system`` surface that reports the date contract and the limits of the
stored data. Kept in ``apps.core`` (rather than ``config.urls``) so the project-level
URLConf stays focused on API routers, matching the existing convention that
cross-app shared helpers live here.
"""

import inspect
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from apps.core.date_contract import (
    ASSET_ETF,
    ASSET_STOCKS,
    ETF_BACKTEST_START,
    ETF_RAW_DATA_FROM,
    ETF_TRAINING_START,
    MIN_TRAIN_SESSIONS,
    PRE_CONTRACT_FLAG,
    STOCK_BACKTEST_START,
    STOCK_RAW_DATA_FROM,
    STOCK_TRAINING_START,
)

DATA_LIMITS_CACHE_KEY = 'system_data_limits'


class SPAFallbackView(TemplateView):
    """Render the Vite SPA shell for any non-API, non-admin path.

    django-vite resolves the correct asset URLs based on ``DJANGO_VITE_DEV_MODE``:

    * ``True`` -- emits absolute ``<script>`` URLs against the Vite dev server
      (``http://localhost:5173/@vite/client``, ``.../src/main.tsx``), so HMR
      keeps working even though the HTML itself came from Django on ``:8000``.
    * ``False`` -- reads ``frontend/dist/.vite/manifest.json`` and emits the
      hashed ``/static/`` URLs produced by ``npm run build``. WhiteNoise serves
      those straight from ``STATIC_ROOT`` after ``collectstatic``.

    Client-side routing (react-router-dom) handles every deep link under the
    catch-all pattern in ``config/urls.py``, so this view never needs to know
    about individual SPA routes.
    """

    template_name = "frontend/index.html"


spa_fallback = SPAFallbackView.as_view()


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _extent(model, field):
    """Earliest and latest ``field`` actually stored.

    These tables hold one row per asset *per session*, so the earliest value marks
    where the oldest individual series begins rather than where coverage of the
    universe begins. ``/data-limits`` labels it as table extent for that reason.
    """
    earliest = model.objects.order_by(field).values_list(field, flat=True).first()
    latest = model.objects.order_by('-' + field).values_list(field, flat=True).first()
    return _iso(earliest), _iso(latest)


def _int_setting(names, default):
    """Resolve an int setting the way the trainers do, reporting where it came from.

    Mirrors ``apps.etf_rotation.train_lightgbm._setting_int`` -- first present and
    int-coercible name wins, otherwise the hardcoded fallback is the live value --
    but without importing that module, whose dependencies belong in a worker.
    """
    if isinstance(names, str):
        names = [names]
    for name in names:
        value = getattr(settings, name, None)
        if value is None:
            continue
        try:
            return int(value), name
        except (TypeError, ValueError):
            continue
    return default, None


def _threshold(key, label, names, default, unit, source):
    value, configured_in = _int_setting(names, default)
    return {
        'key': key,
        'label': label,
        'value': value,
        'unit': unit,
        'source': source,
        'configured_in': configured_in or 'hardcoded fallback',
    }


def _row_estimates():
    """Approximate live-row counts from the Postgres statistics catalog.

    ``Model.objects.count()`` is unusable here: ``analytics_technicalindicator``
    holds ~179M rows and a ``COUNT(*)`` on it runs for minutes, which would put a
    page-load path behind a full scan. ``n_live_tup`` is an estimate maintained by
    the autovacuum stats collector, so the UI labels it as such.
    """
    from django.db import connection

    if connection.vendor != 'postgresql':
        return {}
    with connection.cursor() as cursor:
        cursor.execute('SELECT relname, n_live_tup FROM pg_stat_user_tables')
        return {name: int(count) for name, count in cursor.fetchall()}


def _build_data_limits():
    """Assemble the extent and threshold payload for ``/data-limits``."""
    from apps.analytics.indicator_warmup import MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS
    from apps.analytics.models import SignalEvent, TechnicalIndicator
    from apps.etf_rotation.backtest_engine import CALENDAR_WARMUP_CALENDAR_DAYS
    from apps.etf_rotation.models import (
        ETFDaily, ETFFeatureSnapshot, ETFUniverse, IndexValuationDaily, IndexWeightSnapshot,
    )
    from apps.etf_rotation.walk_forward import expanding_folds
    from apps.factors.models import CapitalFlowSnapshot, FundamentalFactorSnapshot
    from apps.macro.models import MacroSnapshot
    from apps.markets.models import ExchangeTradingCalendar, IndexMembership, OHLCV
    from apps.markets.price_limits import NEW_LISTING_FREE_SESSIONS
    from apps.prediction.models import PredictionResult
    from apps.sentiment.models import NewsArticle

    specs = [
        ('Stock OHLCV', OHLCV, 'date', 'markets_ohlcv has no date-leading index, so the '
         'earliest bound is a sequential scan; it is cached rather than indexed.'),
        ('ETF daily bars', ETFDaily, 'date', ''),
        ('ETF feature snapshots', ETFFeatureSnapshot, 'date', 'Eligible-and-snapshot rows only; '
         'this is the panel the rotation model trains on.'),
        ('Index weights', IndexWeightSnapshot, 'trade_date', ''),
        ('Index valuation', IndexValuationDaily, 'date', ''),
        ('Index membership (PIT)', IndexMembership, 'trade_date', ''),
        ('Trading calendar', ExchangeTradingCalendar, 'trade_date', 'Exchange sessions, not asset rows.'),
        ('Macro snapshots', MacroSnapshot, 'date', ''),
        ('Technical indicators', TechnicalIndicator, 'timestamp', ''),
        ('Signal events', SignalEvent, 'timestamp', ''),
        ('Capital flow', CapitalFlowSnapshot, 'date', ''),
        ('Fundamental factors', FundamentalFactorSnapshot, 'date', ''),
        ('News articles', NewsArticle, 'published_at', ''),
        ('Prediction results', PredictionResult, 'date', ''),
    ]

    datasets = []
    estimates = _row_estimates()
    for label, model, field, note in specs:
        earliest, latest = _extent(model, field)
        datasets.append({
            'label': label,
            'model': f'{model._meta.app_label}.{model.__name__}',
            'field': field,
            'earliest': earliest,
            'latest': latest,
            'rows_estimate': estimates.get(model._meta.db_table),
            'note': note,
        })

    universe = ETFUniverse.objects.exclude(first_trade_date=None)
    etf_coverage = {
        'listed_funds': ETFUniverse.objects.count(),
        'funds_with_first_trade_date': universe.count(),
        'earliest_first_trade_date': _iso(universe.order_by('first_trade_date').values_list(
            'first_trade_date', flat=True).first()),
        'latest_first_trade_date': _iso(universe.order_by('-first_trade_date').values_list(
            'first_trade_date', flat=True).first()),
        'eligible_on_latest_snapshot': ETFFeatureSnapshot.objects.filter(
            is_eligible=True,
            date=ETFFeatureSnapshot.objects.filter(is_eligible=True).order_by(
                '-date').values_list('date', flat=True).first(),
        ).count(),
    }

    train_lightgbm = 'apps/etf_rotation/train_lightgbm.py'
    thresholds = [
        _threshold('etf_min_listing_age_days', 'Minimum listing age', 'ETF_MIN_LISTING_AGE_DAYS',
                   183, 'calendar days', 'config/settings/base.py'),
        _threshold('etf_min_avg_amount_yuan', 'Minimum average turnover', 'ETF_MIN_AVG_AMOUNT_YUAN',
                   50_000_000, 'yuan', 'config/settings/base.py'),
        _threshold('etf_label_horizon_days', 'Label horizon', 'ETF_LABEL_HORIZON_DAYS',
                   20, 'trading sessions', 'config/settings/base.py'),
        _threshold('min_train_sessions', 'Minimum training window', 'MIN_TRAIN_SESSIONS',
                   MIN_TRAIN_SESSIONS, 'trading sessions', 'apps/core/date_contract.py'),
        _threshold('etf_min_train_rows', 'Minimum training samples', 'ETF_MIN_TRAIN_ROWS',
                   200, 'rows', train_lightgbm),
        # Cross-check, not a second source: the signature default must equal the
        # contract constant. If someone re-hardcodes it, this row diverges again.
        _threshold('expanding_folds_min_train_days', 'expanding_folds default',
                   'EXPANDING_FOLDS_MIN_TRAIN_DAYS',
                   inspect.signature(expanding_folds).parameters['min_train_days'].default,
                   'trading sessions', 'apps/etf_rotation/walk_forward.py'),
        _threshold('new_listing_free_sessions', 'Post-IPO sessions with no price limit',
                   'NEW_LISTING_FREE_SESSIONS', NEW_LISTING_FREE_SESSIONS,
                   'trading sessions', 'apps/markets/price_limits.py'),
        _threshold('indicator_prefill', 'Indicator warmup prefill',
                   'MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS', MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
                   'calendar days', 'apps/analytics/indicator_warmup.py'),
        _threshold('backtest_calendar_warmup', 'Backtest calendar warmup',
                   'CALENDAR_WARMUP_CALENDAR_DAYS', CALENDAR_WARMUP_CALENDAR_DAYS,
                   'calendar days', 'apps/etf_rotation/backtest_engine.py'),
    ]

    # Both trainers read ETF_MAX_POSITIONS. They used to read different names -- the
    # LSTM saw the configured 3 while LightGBM saw an unset ETF_ROTATION_MAX_POSITIONS
    # and kept a fallback of 5 -- so two models of one strategy sized different
    # portfolios and every comparison between them was invalid.
    max_positions = dict(
        _threshold('etf_max_positions', 'Max holdings', 'ETF_MAX_POSITIONS',
                   3, 'positions', 'config/settings/base.py'),
        trainers=['train_lightgbm.py', 'train_lstm.py'],
    )

    return {
        'datasets': datasets,
        'etf_coverage': etf_coverage,
        'thresholds': thresholds,
        'max_positions': max_positions,
        'generated_at': timezone.now().isoformat(timespec='seconds'),
    }


def _contract_payload():
    return {
        'escape_hatch_flag': PRE_CONTRACT_FLAG,
        'note': 'Raw-data bounds are observations about stored history, not enforced limits.',
        'asset_classes': [
            {
                'key': ASSET_STOCKS,
                'raw_data_from': STOCK_RAW_DATA_FROM.isoformat(),
                'training_start': STOCK_TRAINING_START.isoformat(),
                'backtest_start': STOCK_BACKTEST_START.isoformat(),
            },
            {
                'key': ASSET_ETF,
                'raw_data_from': ETF_RAW_DATA_FROM.isoformat(),
                'training_start': ETF_TRAINING_START.isoformat(),
                'backtest_start': ETF_BACKTEST_START.isoformat(),
            },
        ],
    }


class SystemInfoViewSet(ViewSet):
    """Read-only introspection of the date contract and the limits of stored data.

    No serializer: both actions return a plain dict of scalars, matching the
    ``ETFRotationSignalViewSet.freshness`` shape. ``date_contract`` reads module
    constants and issues no queries; ``data_limits`` probes extents and is cached,
    because the OHLCV earliest bound is a sequential scan.
    """

    def get_queryset(self):
        return None

    @action(detail=False, methods=['get'], url_path='date-contract')
    def date_contract(self, request):
        return Response(_contract_payload())

    @action(detail=False, methods=['get'], url_path='data-limits')
    def data_limits(self, request):
        ttl = getattr(settings, 'DASHBOARD_SUMMARY_CACHE_SECONDS', 60)
        payload = cache.get(DATA_LIMITS_CACHE_KEY)
        if payload is None:
            payload = _build_data_limits()
            cache.set(DATA_LIMITS_CACHE_KEY, payload, ttl)
        payload = {**payload, 'contract': _contract_payload()}
        return Response(payload)
