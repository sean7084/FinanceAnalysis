"""ETF rotation API surface: the data and signal reads, plus the backtest lifecycle.

Three constraints shape this file beyond ordinary field mapping.

**These tables are large.** ``ETFDaily`` and ``ETFFeatureSnapshot`` each hold millions of
rows, so every queryset ``select_related``s ``ETFUniverse`` (the alternative is one query
per bar) and filters on the columns the migrations actually indexed -- ``date``,
``(etf, date)``, ``date``+``is_eligible``, ``(status, entry_date)``. Unfiltered listing is
allowed but is the caller's problem; the index choice is documented per view.

**No permission classes are declared.** ``config/settings/base.py`` sets
``DEFAULT_PERMISSION_CLASSES`` to ``AllowAny`` with an empty
``DEFAULT_AUTHENTICATION_CLASSES``, so every route here is public and there is no
credential to inherit. For the five read-only viewsets that is harmless. For
``ETFRotationBacktestViewSet`` it means the bound on what a caller can do is the guardrails
in ``create`` -- RULE only, a fingerprint dedupe, a concurrency cap -- and not a permission
layer this app does not have. The API runs on an internal network, so the threat being
managed here is a runaway loop of legitimate requests, not an adversary. Declaring nothing
per view keeps that a single decision in ``base.py``.

**Backtest lifecycle transitions are intent-based, and ETF never revokes.** ``pause``,
``restart`` and ``destroy`` on a RUNNING row write ``pending_control_action`` and return
202; the engine applies the intent at the next chunk boundary. The stock views additionally
revoke the Celery task, and this app deliberately does not. An ETF task owns its whole
chunk loop, so a live task will reach a boundary and honour the request, and a dead one is
already unrevable -- terminating the first discards an in-flight chunk's work, and revoking
a task that has reached a terminal state only makes ``apps.core/task_health.py`` report a
winding-down row as orphaned. A paused-but-still-queued task parks itself: see the PAUSED
re-entry guard in ``apps.etf_rotation.tasks.run_etf_rotation_backtest``.

Pagination is inherited from ``apps.core.pagination.ApiPageNumberPagination`` for the same
reason. Only the backtest view overrides it, because a page of grid-search rows is a table
of numbers and 50 is too small to read.
"""

import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, Throttled, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.core.task_health import get_backtest_run_task_owner_state

from .comparison import build_etf_comparison_payload
from .models import (
    ETFCategory,
    ETFDaily,
    ETFFeatureSnapshot,
    ETFRotationBacktest,
    ETFRotationPosition,
    ETFRotationSignal,
    ETFRotationSignalRun,
    ETFUniverse,
)
from .serializers import (
    ETFDailySerializer,
    ETFFeatureSnapshotSerializer,
    ETFRotationBacktestListSerializer,
    ETFRotationBacktestSerializer,
    ETFRotationPositionSerializer,
    ETFRotationSignalListSerializer,
    ETFRotationSignalSerializer,
    ETFUniverseSerializer,
)


#: Sessions a feature snapshot may trail the newest bar before the defensive-asset dropdown
#: stops quoting its turnover. The feature job runs daily; five sessions covers a long
#: holiday as well, and beyond that the number is not evidence about today's liquidity.
DEFENSIVE_SNAPSHOT_MAX_LAG_SESSIONS = 5


def _date_param(query_params, name):
    """Parse an inclusive ``YYYY-MM-DD`` query param, rejecting a malformed value.

    A silently-ignored ``?start=2024-1-2`` would return a full-table page that looks like a
    correct answer, which is worse than a 400.
    """
    raw = str(query_params.get(name) or '').strip()
    if not raw:
        return None
    parsed = parse_date(raw)
    if parsed is None:
        raise ValidationError({name: f'Invalid {name}: {raw!r}. Expected YYYY-MM-DD.'})
    return parsed


def _int_param(query_params, name):
    raw = str(query_params.get(name) or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValidationError({name: f'Invalid {name}: {raw!r}. Expected an integer.'})


def _parse_extra_compare_run_ids(query_params):
    """Overlay ids from ``?extra_ids=1,2`` or repeated ``?extra_compare_run_id=``.

    Both spellings are accepted because the stock chart component sends the latter and
    the ETF workbench will send the former; an unparseable value is dropped rather than
    raising, since an ignored overlay is a worse surprise than a 400 on a chart request.
    """
    raw_values = []
    for name in ('extra_ids', 'extra_compare_run_ids'):
        csv_value = query_params.get(name)
        if csv_value:
            raw_values.extend(csv_value.split(','))
    raw_values.extend(query_params.getlist('extra_compare_run_id'))

    parsed_ids = []
    seen_ids = set()
    for raw_value in raw_values:
        try:
            run_id = int(str(raw_value).strip())
        except (TypeError, ValueError):
            continue
        if run_id <= 0 or run_id in seen_ids:
            continue
        seen_ids.add(run_id)
        parsed_ids.append(run_id)
    return parsed_ids


# Terminal statuses whose payloads can no longer change, so they are worth caching.
_ETF_CACHEABLE_STATUSES = frozenset({
    ETFRotationBacktest.Status.COMPLETED,
    ETFRotationBacktest.Status.FAILED,
})


def _etf_payload_cache_key(run, suffix, extra_compare_run_ids=()):
    """Cache key for a run's derived payload.

    Mirrors ``apps.backtest.views._backtest_payload_cache_key``: status and
    ``updated_at`` are in the key so any transition or write produces a new one instead
    of serving a stale body, and a non-terminal run returns None because its curve is
    still being appended to.
    """
    if run.status not in _ETF_CACHEABLE_STATUSES:
        return None
    compare_fragment = ','.join(str(run_id) for run_id in sorted(extra_compare_run_ids))
    return f'etf_backtest_{suffix}:{run.id}:{run.status}:{run.updated_at.isoformat()}:{compare_fragment}'


# A row that has not reached a terminal state still holds a slot on the etf-rotation
# worker, so both the concurrency cap and the dedupe scan are about these two statuses.
# PAUSED is excluded on purpose: it holds a cursor in ``report``, not a worker.
_ETF_INFLIGHT_STATUSES = (
    ETFRotationBacktest.Status.PENDING,
    ETFRotationBacktest.Status.RUNNING,
)
DEFAULT_ETF_MAX_CONCURRENT_BACKTESTS = 2


def _has_stale_task_owner(run):
    return get_backtest_run_task_owner_state(run)['has_stale_task_owner']


def _etf_max_concurrent_backtests():
    """Bound on in-flight ETF backtests; a value <= 0 refuses every creation (fail closed)."""
    raw_value = getattr(settings, 'ETF_MAX_CONCURRENT_BACKTESTS', DEFAULT_ETF_MAX_CONCURRENT_BACKTESTS)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_ETF_MAX_CONCURRENT_BACKTESTS


def _backtest_config_for(run):
    """Rebuild the engine's config from a row's own columns, saved or not.

    Mirrors exactly what ``apps.etf_rotation.tasks.run_etf_rotation_backtest`` feeds the
    engine -- the explicit cost/identity knobs plus ``weighting``, ``dedup_track_index``,
    ``max_per_category`` and ``walk_forward_steps`` as overrides -- which is what makes the
    digest computed here the digest the worker will use. Two details are load bearing: the
    selection knobs must be passed explicitly because the row's column default and the
    dataclass default are only equal by construction (a rebuilt config that dropped
    ``weighting`` would silently re-default it, and the duplicate-run digest would then
    describe a run nobody asked for), and the two Decimal columns are cast to ``float`` the
    way the task casts them, or the JSON digest differs by quoting alone.

    Imported lazily in both directions: ``backtest_engine`` pulls in pandas and TA-Lib, and
    ``tasks`` pulls in tushare, and this module is reached from ``config/urls.py`` the
    moment the web process starts.
    """
    from .backtest_engine import BacktestConfig

    return BacktestConfig(
        start_date=run.start_date,
        end_date=run.end_date,
        model_type=run.model_type,
        rebalance_frequency=run.rebalance_frequency,
        max_positions=run.max_positions,
        momentum_lookback=run.momentum_lookback,
        absolute_momentum_filter=run.absolute_momentum_filter,
        defensive_asset=run.defensive_asset,
        initial_capital=float(run.initial_capital),
        commission_rate=float(run.commission_rate),
        min_commission=float(run.min_commission),
        slippage=float(run.slippage),
        liquidity_floor_multiple=float(run.liquidity_floor_multiple),
        min_trade_notional_fraction=float(run.min_trade_notional_fraction),
        drift_band_fraction=float(run.drift_band_fraction),
        liquidity_mode=run.liquidity_mode,
        adv_participation_cap=float(run.adv_participation_cap),
        kelly_fraction=float(run.kelly_fraction),
        kelly_cap=float(run.kelly_cap),
        kelly_floor=float(run.kelly_floor),
        kelly_lookback_trades=int(run.kelly_lookback_trades),
        cash_yield_annual=float(run.cash_yield_annual),
        benchmark_code=run.benchmark_code,
        weighting=run.weighting,
        dedup_track_index=run.dedup_track_index,
        max_per_category=run.max_per_category,
        walk_forward_steps=run.walk_forward_steps,
    )


def _parameters_digest(parameters):
    """Reproduce ``BacktestConfig.fingerprint()`` from a row's stored parameter blob.

    ``_adopt_backtest_row`` writes ``config.to_parameters()`` into ``parameters`` verbatim
    (``backtest_engine.py:1702``) and the fingerprint is a digest of that same dict, so a
    row's own JSON column identifies its configuration with no engine import per candidate.
    An empty blob -- a row created before ``create`` started persisting parameters -- simply
    cannot match, which fails safe: the worst case is a duplicate run, not a refused one.
    """
    if not parameters:
        return ''
    payload = json.dumps(parameters, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _find_in_flight_duplicate(config):
    """An in-flight row that is the same computation as ``config``, or None.

    The WHERE clause is a cheap pre-screen on the columns that are both filterable and
    load bearing; ``fingerprint()`` is the actual test, because equality also has to cover
    the fee knobs and the selection parameters that would need an unreadably long query.
    """
    candidates = ETFRotationBacktest.objects.filter(
        status__in=_ETF_INFLIGHT_STATUSES,
        start_date=config.start_date,
        end_date=config.end_date,
        model_type=config.model_type,
        rebalance_frequency=config.rebalance_frequency,
        max_positions=config.max_positions,
        momentum_lookback=config.momentum_lookback,
        absolute_momentum_filter=config.absolute_momentum_filter,
        defensive_asset=config.defensive_asset,
        weighting=config.weighting,
        benchmark_code=config.benchmark_code,
    )
    digest = config.fingerprint()
    for candidate in candidates.only('id', 'parameters').order_by('id'):
        if _parameters_digest(candidate.parameters) == digest:
            return ETFRotationBacktest.objects.get(pk=candidate.id)
    return None


def _queue_etf_rotation_run(run):
    from .tasks import queue_etf_rotation_run

    return queue_etf_rotation_run(run)


class ETFUniverseViewSet(viewsets.ReadOnlyModelViewSet):
    """Fund directory, including delisted funds (they are what makes a backtest honest)."""

    serializer_class = ETFUniverseSerializer

    def get_queryset(self):
        qs = ETFUniverse.objects.all().order_by('ts_code')
        params = self.request.query_params
        category = params.get('category')
        if category:
            qs = qs.filter(category=category)
        sub_category = params.get('sub_category')
        if sub_category:
            qs = qs.filter(sub_category=sub_category)
        fund_type = params.get('fund_type')
        if fund_type:
            qs = qs.filter(fund_type=fund_type)
        invest_type = params.get('invest_type')
        if invest_type:
            qs = qs.filter(invest_type=invest_type)
        management = params.get('management')
        if management:
            qs = qs.filter(management__icontains=management)
        ts_code = params.get('ts_code')
        if ts_code:
            qs = qs.filter(ts_code=ts_code.upper())
        search = params.get('search')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(ts_code__icontains=search.upper())
                            | Q(benchmark__icontains=search))
        listed_before = _date_param(params, 'listed_before')
        if listed_before is not None:
            # ``effective_list_date`` is a property, so the C6 fallback has to be
            # reproduced in SQL rather than trusted to the Python side.
            qs = qs.filter(Q(list_date__lte=listed_before) | Q(
                list_date__isnull=True, first_trade_date__lte=listed_before))
        for flag in ('is_active', 'is_rankable'):
            raw = params.get(flag)
            if raw is not None and raw != '':
                qs = qs.filter(**{flag: raw.lower() in {'1', 'true'}})
        return qs

    @action(detail=False, methods=['get'])
    def defensive_assets(self, request):
        """Money-market ETFs that can actually serve as the defensive sleeve, richest first.

        ``defensive_asset`` used to be free text with a hardcoded ``'511880.SH'`` behind it,
        which let a run be created against a code that has no bars -- or against a money fund
        so small that the whole book's risk-off leg could not be filled without moving it.
        The workbench replaces that text box with this list, and each row carries the number
        the answer turns on: ``avg_amount_20d`` in 元 next to the liquidity floor the engine
        will apply to it.

        ``avg_amount_20d`` is read from the newest eligible cross-section rather than
        re-derived from bars so the dropdown states the same figure the ranker used. A fund
        with no snapshot yet is still listed, with ``eligible=False`` and a null turnover,
        because "this fund exists but has no computed features" is a different answer from
        "this fund is too illiquid" and the page should not collapse them.
        """
        from .features import MIN_AVG_AMOUNT_YUAN_DEFAULT

        newest_snapshot_date = (
            ETFFeatureSnapshot.objects.filter(is_eligible=True)
            .order_by('-date').values_list('date', flat=True).first()
        )
        # The feature job writes the snapshot; it does not have to be current for this list
        # to answer, but it does have to be recent enough that quoting its turnover is not
        # misleading. Past this lag the figure is reported as unknown rather than trusted.
        newest_bar_date = (
            ETFDaily.objects.order_by('-date').values_list('date', flat=True).first()
        )
        snapshot_lag_sessions = 0
        if newest_snapshot_date is not None and newest_bar_date is not None:
            snapshot_lag_sessions = (
                ETFDaily.objects.filter(
                    date__gt=newest_snapshot_date, date__lte=newest_bar_date,
                ).values('date').distinct().count()
            )
        turnover_by_code = {}
        if (newest_snapshot_date is not None
                and snapshot_lag_sessions <= DEFENSIVE_SNAPSHOT_MAX_LAG_SESSIONS):
            rows = (
                ETFFeatureSnapshot.objects.filter(
                    date=newest_snapshot_date, is_eligible=True,
                ).values('etf__ts_code', 'avg_amount_20d')
            )
            turnover_by_code = {row['etf__ts_code']: row['avg_amount_20d'] for row in rows}

        floor = float(getattr(settings, 'ETF_MIN_AVG_AMOUNT_YUAN', MIN_AVG_AMOUNT_YUAN_DEFAULT))
        results = [
            {
                'ts_code': row.ts_code,
                'name': row.name,
                'avg_amount_20d': turnover_by_code.get(row.ts_code),
                'eligible': row.ts_code in turnover_by_code,
                'min_avg_amount_yuan': floor,
            }
            for row in ETFUniverse.objects.filter(
                category=ETFCategory.MONEY, is_rankable=True,
            ).order_by('ts_code')
        ]
        results.sort(
            key=lambda item: item['avg_amount_20d'] if item['avg_amount_20d'] is not None else -1.0,
            reverse=True,
        )
        return Response({'results': results})


class ETFDailyViewSet(viewsets.ReadOnlyModelViewSet):
    """Daily bars in 份/元 after the ingest unit conversion.

    Reachable by ``etf``/``ts_code`` (uses the ``(etf, date)`` index) or by ``date``
    (uses the ``date`` index); a bare list walks ``-date`` across the whole table.
    """

    serializer_class = ETFDailySerializer

    def get_queryset(self):
        qs = ETFDaily.objects.select_related('etf').all().order_by('-date', 'etf_id')
        params = self.request.query_params
        etf_id = _int_param(params, 'etf')
        if etf_id is not None:
            qs = qs.filter(etf_id=etf_id)
        ts_code = params.get('ts_code')
        if ts_code:
            qs = qs.filter(etf__ts_code=ts_code.upper())
        exact_date = _date_param(params, 'date')
        if exact_date is not None:
            qs = qs.filter(date=exact_date)
        start = _date_param(params, 'start')
        if start is not None:
            qs = qs.filter(date__gte=start)
        end = _date_param(params, 'end')
        if end is not None:
            qs = qs.filter(date__lte=end)
        return qs


class ETFFeatureSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """Point-in-time features plus the look-only label columns.

    ``eligible=true`` combined with ``start``/``end`` is served by the
    ``(date, is_eligible)`` index and is how the rankable cross-section for a window is
    pulled without transferring rows that were screened out.
    """

    serializer_class = ETFFeatureSnapshotSerializer

    def get_queryset(self):
        qs = ETFFeatureSnapshot.objects.select_related('etf').all().order_by('-date', 'etf_id')
        params = self.request.query_params
        etf_id = _int_param(params, 'etf')
        if etf_id is not None:
            qs = qs.filter(etf_id=etf_id)
        ts_code = params.get('ts_code')
        if ts_code:
            qs = qs.filter(etf__ts_code=ts_code.upper())
        exact_date = _date_param(params, 'date')
        if exact_date is not None:
            qs = qs.filter(date=exact_date)
        start = _date_param(params, 'start')
        if start is not None:
            qs = qs.filter(date__gte=start)
        end = _date_param(params, 'end')
        if end is not None:
            qs = qs.filter(date__lte=end)
        eligible = params.get('eligible')
        if eligible is not None and eligible != '':
            qs = qs.filter(is_eligible=eligible.lower() in {'1', 'true'})
        horizon = _int_param(params, 'label_horizon_days')
        if horizon is not None:
            qs = qs.filter(label_horizon_days=horizon)
        return qs


class ETFRotationSignalViewSet(viewsets.ReadOnlyModelViewSet):
    """Rotation rankings and selections.

    ``unique_together ('date', 'signal_type')`` means one row per selector per session, so
    ``latest`` is well-defined: the newest ``date`` after the caller's filters, never a
    tie broken by whichever duplicate a write happened to leave behind.
    """

    def get_serializer_class(self):
        return ETFRotationSignalSerializer if self.detail else ETFRotationSignalListSerializer

    def get_queryset(self):
        qs = ETFRotationSignal.objects.all()
        params = self.request.query_params
        signal_type = params.get('signal_type')
        if signal_type:
            qs = qs.filter(signal_type=signal_type.upper())
        else:
            # The shadow book is a measurement, not production. Hiding it by default keeps
            # ``latest``/``freshness`` on the signal the operator acts on; asking for
            # ``?signal_type=SHADOW`` is how the rematch is inspected.
            qs = qs.exclude(signal_type=ETFRotationSignal.SignalType.SHADOW)
        model_type = params.get('model_type')
        if model_type:
            qs = qs.filter(model_type=model_type.upper())
        exact_date = _date_param(params, 'date')
        if exact_date is not None:
            qs = qs.filter(date=exact_date)
        start = _date_param(params, 'start')
        if start is not None:
            qs = qs.filter(date__gte=start)
        end = _date_param(params, 'end')
        if end is not None:
            qs = qs.filter(date__lte=end)
        max_positions = _int_param(params, 'max_positions')
        if max_positions is not None:
            qs = qs.filter(max_positions=max_positions)
        return qs.order_by('-date', 'signal_type')

    @action(detail=False, methods=['get'])
    def latest(self, request):
        signal = self.get_queryset().order_by('-date', '-created_at').first()
        if signal is None:
            raise NotFound('No rotation signal matches these filters; generate one first.')
        return Response(ETFRotationSignalSerializer(signal).data)

    @action(detail=False, methods=['get'])
    def freshness(self, request):
        """How many sessions the newest signal trails the newest eligible cross-section.

        ``ETFRotationSignal.date`` is an as-of date, not a generation time, and the page
        labels it as one. That is only honest while the two agree: on 2026-10-22 the newest
        signal said 10-20 while fully-computed snapshots existed for 10-22, and nothing on
        screen distinguished "the market told us to hold" from "the scheduled job never
        fired". This endpoint makes that distinction one cheap GET.

        The lag counts ``ETFDaily`` sessions strictly after the signal date and up to the
        newest eligible snapshot, i.e. sessions the strategy could have been re-planned on.
        Zero means the signal is current, whatever the calendar says.
        """
        from apps.etf_rotation.signal_runs import latest_feed_stale

        signal = self.get_queryset().order_by('-date', '-created_at').first()
        newest_snapshot = (
            ETFFeatureSnapshot.objects.filter(is_eligible=True)
            .order_by('-date').values_list('date', flat=True).first()
        )
        newest_bar = (
            ETFDaily.objects.order_by('-date').values_list('date', flat=True).first()
        )
        lag_sessions = 0
        if signal is not None and newest_snapshot is not None:
            lag_sessions = len(
                set(
                    ETFDaily.objects.filter(
                        date__gt=signal.date, date__lte=newest_snapshot,
                    ).values_list('date', flat=True)
                )
            )
        # The last time the *signal job* ran, whatever it concluded. ``signal_date`` says
        # what the strategy saw; this says whether anything has tried since. Health rows are
        # excluded: a feed watchdog is not a signal attempt and must not make the page say
        # the last signal job failed.
        latest_run = (
            ETFRotationSignalRun.objects
            .exclude(signal_type=ETFRotationSignalRun.SignalType.HEALTH)
            .order_by('-created_at').first()
        )
        feed_stale = latest_feed_stale(newest_bar)
        return Response({
            'signal_date': signal.date if signal is not None else None,
            'latest_snapshot_date': newest_snapshot,
            'latest_bar_date': newest_bar,
            'lag_sessions': int(lag_sessions),
            'is_stale': bool(lag_sessions >= 1),
            'model_version': signal.model_version if signal is not None else None,
            # The last time the job *ran*, whatever it concluded. ``signal_date`` says what
            # the strategy saw; this says whether anything has tried since.
            'last_attempt': None if latest_run is None else {
                'date': latest_run.date,
                'signal_type': latest_run.signal_type,
                'outcome': latest_run.outcome,
                'reason_code': latest_run.reason_code,
                'message': latest_run.message,
                'created_at': latest_run.created_at,
            },
            # A feed defect is not a signal lag: the signal can be built on the newest
            # snapshot and still be blind if the benchmark behind it is a session behind.
            'feed_stale': None if feed_stale is None else {
                'date': feed_stale.date,
                'reason_code': feed_stale.reason_code,
                'message': feed_stale.message,
                'created_at': feed_stale.created_at,
            },
        })


class ETFRotationPositionViewSet(viewsets.ReadOnlyModelViewSet):
    """Open and closed legs attributed to the signal that opened them."""

    serializer_class = ETFRotationPositionSerializer

    def get_queryset(self):
        qs = ETFRotationPosition.objects.select_related('etf', 'signal').all()
        params = self.request.query_params
        # A shadow leg lives in the same table but is a different book. The realised-PnL
        # card sums CLOSED rows, so mixing the two would report the rematch as production
        # performance. ``signal_type`` asks for one book explicitly; the default is the
        # production book alone.
        shadow = Q(signal__signal_type='SHADOW') | Q(metadata__signal_type='SHADOW')
        signal_type = params.get('signal_type')
        if signal_type:
            wanted = signal_type.upper()
            qs = qs.filter(Q(signal__signal_type=wanted) | Q(metadata__signal_type=wanted))
        else:
            qs = qs.exclude(shadow)
        status_value = params.get('status')
        if status_value:
            qs = qs.filter(status=status_value.upper())
        etf_id = _int_param(params, 'etf')
        if etf_id is not None:
            qs = qs.filter(etf_id=etf_id)
        signal_id = _int_param(params, 'signal')
        if signal_id is not None:
            qs = qs.filter(signal_id=signal_id)
        start = _date_param(params, 'start')
        if start is not None:
            qs = qs.filter(entry_date__gte=start)
        end = _date_param(params, 'end')
        if end is not None:
            qs = qs.filter(entry_date__lte=end)
        return qs.order_by('-entry_date', 'id')


class ETFRotationBacktestPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 200


class ETFRotationBacktestViewSet(viewsets.ModelViewSet):
    """Backtest runs: the grid's read surface plus the full launch lifecycle.

    List drops ``equity_curve``/``trade_log``/``report``: a ``--grid`` sweep writes one row
    per RULE combination and each of those columns grows with the window, so the list
    endpoint would otherwise be the most expensive page in the app. Progress lives in
    ``report['runtime_state']['cursor']``, which the list payload does not carry -- so a
    polling client GETs the detail row. That is a consequence of the list shape, not an
    inconvenience to work around.

    ``http_method_names`` omits PUT and PATCH on purpose. A run's configuration *is* its
    identity: the fingerprint in ``report['runtime_state']`` is what lets a paused row
    resume, so editing the window or the fee knobs of a RUNNING row would resume a
    different computation under resumable state belonging to another one. Change a run by
    creating a new one.
    """

    pagination_class = ETFRotationBacktestPagination
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_serializer_class(self):
        return ETFRotationBacktestSerializer if self.detail else ETFRotationBacktestListSerializer

    def get_queryset(self):
        qs = ETFRotationBacktest.objects.all()
        params = self.request.query_params
        status_value = params.get('status')
        if status_value:
            qs = qs.filter(status=status_value.upper())
        model_type = params.get('model_type')
        if model_type:
            qs = qs.filter(model_type=model_type.upper())
        frequency = params.get('rebalance_frequency')
        if frequency:
            qs = qs.filter(rebalance_frequency=frequency.upper())
        # ``weighting`` is the one dimension the grid sweeps that is stored lowercase
        # ('equal'/'score'), so it is normalized the other way from the two above.
        weighting = params.get('weighting')
        if weighting:
            qs = qs.filter(weighting=weighting.strip().lower())
        # The 2026-10 rows written while ``--no-dedup-track-index`` was silently applied are
        # a measurement set, not baselines. They describe themselves in this column, so the
        # way out of the leaderboard is a filter rather than a name-based exclusion:
        # ``?dedup_track_index=true`` returns the production-rule rows alone. No default
        # (the intended dedup-off control must stay visible when nobody asks).
        dedup = params.get('dedup_track_index')
        if dedup is not None and str(dedup).strip() != '':
            qs = qs.filter(
                dedup_track_index=str(dedup).strip().lower() in ('1', 'true', 'yes', 'on'),
            )
        start = _date_param(params, 'window_start')
        if start is not None:
            qs = qs.filter(start_date__gte=start)
        end = _date_param(params, 'window_end')
        if end is not None:
            qs = qs.filter(end_date__lte=end)
        name = params.get('name')
        if name:
            qs = qs.filter(name__icontains=name)
        # ``sort=alpha`` is the ranking the grid command prints its leaderboard by, so a
        # client can ask for the same ordering rather than re-sorting a truncated page.
        # nulls_last is not cosmetic: PostgreSQL defaults to NULLS FIRST for DESC, so an
        # aborted run that never computed a metric would otherwise head the leaderboard.
        sort = (params.get('sort') or '').strip().lower()
        allowed_sorts = {
            'alpha': F('alpha_vs_benchmark').desc(nulls_last=True),
            'sharpe': F('sharpe_ratio').desc(nulls_last=True),
            'return': F('total_return').desc(nulls_last=True),
            'drawdown': F('max_drawdown').asc(nulls_last=True),
            'created': '-created_at',
        }
        if sort in allowed_sorts:
            qs = qs.order_by(allowed_sorts[sort], 'id')
        else:
            qs = qs.order_by('-created_at')
        return qs

    def create(self, request, *args, **kwargs):
        """Launch a RULE backtest: validate, dedupe, bound, persist, enqueue.

        The order is the point. A duplicate consumes no slot, so a client that retries a
        POST it already has running gets its row back rather than a 429 it cannot act on.
        ``parameters`` is persisted here rather than left to the engine because a
        queued-but-unstarted row has an empty blob until ``_adopt_backtest_row`` runs, and
        without it the second of two back-to-back POSTs cannot be recognised as the same
        computation as the first.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        config = _backtest_config_for(ETFRotationBacktest(**serializer.validated_data))

        duplicate = _find_in_flight_duplicate(config)
        if duplicate is not None:
            # 200, not 202: nothing was queued, so there is no new work accepted.
            return Response(self.get_serializer(duplicate).data, status=status.HTTP_200_OK)

        cap = _etf_max_concurrent_backtests()
        in_flight = ETFRotationBacktest.objects.filter(status__in=_ETF_INFLIGHT_STATUSES).count()
        if in_flight >= cap:
            raise Throttled(detail=(
                f'{in_flight} ETF backtest(s) are already in flight, which is the cap ({cap}). '
                'Wait for one to finish, pause it, or raise ETF_MAX_CONCURRENT_BACKTESTS.'
            ))

        run = serializer.save(parameters=config.to_parameters())
        transaction.on_commit(lambda: _queue_etf_rotation_run(run))
        return Response(self.get_serializer(run).data, status=status.HTTP_202_ACCEPTED)

    def _queue_existing_run(self, run):
        transaction.on_commit(lambda: _queue_etf_rotation_run(run))

    def _restart_run(self, run):
        """Rerun from session zero, either as an intent or as an immediate re-queue.

        A RUNNING row with a live owner is not reset here: the engine holds state in local
        variables mid-chunk, so wiping the row out from under it would be overwritten by
        the next ``_persist_progress``. The RESTART intent is what makes the engine hand
        the halt back to the task wrapper, which does the wipe once the loop has stopped.
        """
        stale_task_owner = run.status == ETFRotationBacktest.Status.RUNNING and _has_stale_task_owner(run)

        if run.status == ETFRotationBacktest.Status.RUNNING and not stale_task_owner:
            run.pending_control_action = ETFRotationBacktest.ControlAction.RESTART
            run.error_message = ''
            run.save(update_fields=['pending_control_action', 'error_message', 'updated_at'])
            return Response(
                {'message': 'ETF backtest restart requested. It will restart after the current chunk.',
                 'id': run.id},
                status=status.HTTP_202_ACCEPTED,
            )

        from .tasks import reset_etf_rotation_run_for_restart

        reset_etf_rotation_run_for_restart(run)
        self._queue_existing_run(run)
        if stale_task_owner:
            return Response(
                {'message': 'ETF backtest restart queued after recovering dead task ownership.', 'id': run.id},
                status=status.HTTP_202_ACCEPTED,
            )
        return Response({'message': 'ETF backtest restart queued.', 'id': run.id},
                        status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def rerun(self, request, pk=None):
        return self._restart_run(self.get_object())

    @action(detail=True, methods=['post'])
    def restart(self, request, pk=None):
        return self._restart_run(self.get_object())

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """Park ``run`` with its resumable state intact.

        Two shapes, and the difference is not cosmetic. PENDING (and a RUNNING row whose
        worker is gone) has no live chunk to protect, so it is paused in place -- which is
        also what stops a queued task from starting the work, because the task re-reads
        PAUSED before it touches the engine. RUNNING writes the intent and lets the
        chunk-boundary check persist the cursor on its way out.
        """
        run = self.get_object()
        stale_task_owner = run.status == ETFRotationBacktest.Status.RUNNING and _has_stale_task_owner(run)
        if run.status not in {ETFRotationBacktest.Status.PENDING, ETFRotationBacktest.Status.RUNNING}:
            return Response({'message': 'Only pending or running ETF backtests can be paused.'},
                            status=status.HTTP_400_BAD_REQUEST)

        if run.status == ETFRotationBacktest.Status.PENDING or stale_task_owner:
            run.status = ETFRotationBacktest.Status.PAUSED
            run.pending_control_action = ETFRotationBacktest.ControlAction.NONE
            update_fields = ['status', 'pending_control_action', 'updated_at']
            if stale_task_owner:
                run.current_task_id = ''
                run.completed_at = None
                run.error_message = ''
                update_fields.extend(['current_task_id', 'completed_at', 'error_message'])
            run.save(update_fields=update_fields)
            if stale_task_owner:
                return Response({'message': 'ETF backtest paused after recovering dead task ownership.',
                                 'id': run.id}, status=status.HTTP_202_ACCEPTED)
            return Response({'message': 'ETF backtest paused.', 'id': run.id},
                            status=status.HTTP_202_ACCEPTED)

        run.pending_control_action = ETFRotationBacktest.ControlAction.PAUSE
        run.save(update_fields=['pending_control_action', 'updated_at'])
        return Response(
            {'message': 'ETF backtest pause requested. It will pause after the current chunk.', 'id': run.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        run = self.get_object()
        if run.status != ETFRotationBacktest.Status.PAUSED:
            return Response({'message': 'Only paused ETF backtests can be resumed.'},
                            status=status.HTTP_400_BAD_REQUEST)

        run.status = ETFRotationBacktest.Status.PENDING
        run.pending_control_action = ETFRotationBacktest.ControlAction.NONE
        run.error_message = ''
        run.completed_at = None
        run.save(update_fields=['status', 'pending_control_action', 'error_message',
                                'completed_at', 'updated_at'])
        self._queue_existing_run(run)
        return Response({'message': 'ETF backtest resume queued.', 'id': run.id},
                        status=status.HTTP_202_ACCEPTED)

    def destroy(self, request, *args, **kwargs):
        """Delete now, or ask to be deleted at the next boundary.

        Deleting a RUNNING row outright would leave the engine writing into a row that is
        gone -- ``_persist_progress`` would re-INSERT it under the same primary key, and
        the ledger would come back as a zombie with no owner. The DELETE intent makes the
        loop stop and delete itself instead.
        """
        run = self.get_object()
        stale_task_owner = run.status == ETFRotationBacktest.Status.RUNNING and _has_stale_task_owner(run)

        if run.status == ETFRotationBacktest.Status.RUNNING and not stale_task_owner:
            run.pending_control_action = ETFRotationBacktest.ControlAction.DELETE
            run.save(update_fields=['pending_control_action', 'updated_at'])
            return Response(
                {'message': 'ETF backtest removal requested. It will be removed after the current chunk.',
                 'id': run.id},
                status=status.HTTP_202_ACCEPTED,
            )

        self.perform_destroy(run)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'])
    def comparison_curve(self, request, pk=None):
        """Strategy and benchmark curves rescaled onto the run's opening equity.

        Detail-only on purpose: a full window is a few thousand points per series, so
        this belongs nowhere near the list payload. The benchmarks are recomputed from
        ``markets.BenchmarkIndexDaily`` rather than read off ``report``, which is what
        makes a row that ran before this endpoint existed still chartable.
        """
        run = self.get_object()
        extra_compare_run_ids = _parse_extra_compare_run_ids(request.query_params)
        cache_key = _etf_payload_cache_key(run, 'comparison_curve', extra_compare_run_ids)
        if cache_key is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                return Response(cached)

        payload = build_etf_comparison_payload(run, extra_compare_run_ids=extra_compare_run_ids)
        if cache_key is not None:
            cache.set(cache_key, payload, settings.BACKTEST_PAYLOAD_CACHE_SECONDS)
        return Response(payload)
