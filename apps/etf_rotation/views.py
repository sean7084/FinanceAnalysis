"""ETF rotation API surface: read-only views over the data, signals and backtests.

Two constraints shape this file beyond ordinary field mapping.

**These tables are large.** ``ETFDaily`` and ``ETFFeatureSnapshot`` each hold millions of
rows, so every queryset ``select_related``s ``ETFUniverse`` (the alternative is one query
per bar) and filters on the columns the migrations actually indexed -- ``date``,
``(etf, date)``, ``date``+``is_eligible``, ``(status, entry_date)``. Unfiltered listing is
allowed but is the caller's problem; the index choice is documented per view.

**No permission classes are declared.** ``config/settings/base.py`` sets
``DEFAULT_PERMISSION_CLASSES`` to ``AllowAny`` with an empty
``DEFAULT_AUTHENTICATION_CLASSES``, so every route here is public and there is no
credential to inherit. ``ReadOnlyModelViewSet`` is therefore the *only* thing keeping
these surfaces read-only: adding a write action would make it publicly writable, with
no permission layer in front of it. Declaring nothing per view keeps that a single
decision in ``base.py`` instead of implying a protection this app does not have.

Pagination is inherited from ``apps.core.pagination.ApiPageNumberPagination`` for the same
reason. Only the backtest view overrides it, because a page of grid-search rows is a table
of numbers and 50 is too small to read.
"""

from django.db.models import F, Q
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import (
    ETFDaily,
    ETFFeatureSnapshot,
    ETFRotationBacktest,
    ETFRotationPosition,
    ETFRotationSignal,
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


class ETFRotationPositionViewSet(viewsets.ReadOnlyModelViewSet):
    """Open and closed legs attributed to the signal that opened them."""

    serializer_class = ETFRotationPositionSerializer

    def get_queryset(self):
        qs = ETFRotationPosition.objects.select_related('etf', 'signal').all()
        params = self.request.query_params
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


class ETFRotationBacktestViewSet(viewsets.ReadOnlyModelViewSet):
    """Backtest runs, including the resumable runtime state in the detail payload.

    List drops ``equity_curve``/``trade_log``/``report``: a ``--grid`` sweep writes one row
    per RULE combination and each of those columns grows with the window, so the list
    endpoint would otherwise be the most expensive page in the app.
    """

    pagination_class = ETFRotationBacktestPagination

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
        start = _date_param(params, 'window_start')
        if start is not None:
            qs = qs.filter(start_date__gte=start)
        end = _date_param(params, 'window_end')
        if end is not None:
            qs = qs.filter(end_date__lte=end)
        name = params.get('name')
        if name:
            qs = qs.filter(name__icontains=name)
        # 'best' is the ordering the grid command prints, and a client should be able to
        # ask for the same thing rather than re-sorting a truncated page.
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
