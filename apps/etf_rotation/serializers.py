"""ETF rotation API serialisation.

Every viewset in this app is read-only, so there is no validation layer here in the
mould of ``apps/backtest/serializers.py``. What these classes do decide instead is
**what a list response costs**:

``ETFDaily`` and ``ETFFeatureSnapshot`` are multi-million-row tables joined to
``ETFUniverse``, so the fund identity is flattened onto every row (``etf_ts_code``,
``etf_name``) rather than making a client fetch the universe for each bar.

A signal's ``rankings`` is one entry per rankable fund -- ~500 objects per row -- and a
backtest's ``equity_curve``/``trade_log``/``report`` grow with the window and the number
of legs. All four are detail-only: list payloads carry the summary columns and the
selection, never the blobs.

The feature column list is spelled out rather than imported from ``features.FEATURE_COLUMNS``,
because that module imports TA-Lib and pandas at module scope and serializers are
reachable from ``config/urls.py``. Adding a feature therefore needs a deliberate edit
here; a silently-growing API response is the worse failure.
"""

from rest_framework import serializers

from .models import (
    ETFDaily,
    ETFFeatureSnapshot,
    ETFRotationBacktest,
    ETFRotationPosition,
    ETFRotationSignal,
    ETFUniverse,
)


FEATURE_API_COLUMNS = [
    'mom_5d', 'mom_10d', 'mom_20d', 'mom_60d', 'mom_120d', 'mom_250d', 'mom_accel',
    'vol_20d', 'vol_60d', 'sharpe_20d', 'sharpe_60d',
    'rs_vs_benchmark_20d', 'rs_vs_benchmark_60d', 'corr_with_benchmark_60d',
    'rank_pct_20d', 'rank_pct_60d',
    'rsi_14', 'ma5_ratio', 'ma20_ratio', 'ma60_ratio', 'bollinger_pos',
    'drawdown_from_high_60d', 'avg_amount_20d', 'volume_ratio_5d_20d',
]

# Read-only labels describing what the row was trained against. They are exposed so a
# caller can see the horizon, but no feature may be derived from them.
LABEL_API_COLUMNS = ['label_horizon_days', 'fwd_return', 'label_rank_pct', 'label_top_bucket']


class ETFUniverseSerializer(serializers.ModelSerializer):
    effective_list_date = serializers.DateField(read_only=True)

    class Meta:
        model = ETFUniverse
        fields = [
            'id', 'ts_code', 'symbol', 'name', 'fund_type', 'invest_type', 'benchmark',
            'management', 'custodian', 'list_date', 'delist_date', 'first_trade_date',
            'effective_list_date', 'status', 'is_active', 'is_rankable', 'category',
            'sub_category', 'm_fee', 'c_fee', 'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ETFDailySerializer(serializers.ModelSerializer):
    etf_ts_code = serializers.CharField(source='etf.ts_code', read_only=True)
    etf_name = serializers.CharField(source='etf.name', read_only=True)

    class Meta:
        model = ETFDaily
        fields = [
            'id', 'etf', 'etf_ts_code', 'etf_name', 'date',
            'open', 'high', 'low', 'close', 'adj_close',
            'volume', 'amount', 'adj_factor',
        ]
        read_only_fields = fields


class ETFFeatureSnapshotSerializer(serializers.ModelSerializer):
    etf_ts_code = serializers.CharField(source='etf.ts_code', read_only=True)
    etf_name = serializers.CharField(source='etf.name', read_only=True)

    class Meta:
        model = ETFFeatureSnapshot
        fields = [
            'id', 'etf', 'etf_ts_code', 'etf_name', 'date', 'is_eligible',
            *FEATURE_API_COLUMNS,
            *LABEL_API_COLUMNS,
            'metadata',
        ]
        read_only_fields = fields


class ETFRotationSignalSerializer(serializers.ModelSerializer):
    """Full signal, including the ranking that produced the selection."""

    class Meta:
        model = ETFRotationSignal
        fields = [
            'id', 'date', 'signal_type', 'model_type', 'rankings', 'selected_etfs',
            'defensive_allocation', 'defensive_asset', 'model_version',
            'benchmark_return_20d', 'max_positions', 'metadata',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ETFRotationSignalListSerializer(ETFRotationSignalSerializer):
    class Meta(ETFRotationSignalSerializer.Meta):
        fields = [
            f for f in ETFRotationSignalSerializer.Meta.fields if f != 'rankings'
        ]


class ETFRotationPositionSerializer(serializers.ModelSerializer):
    etf_ts_code = serializers.CharField(source='etf.ts_code', read_only=True)
    etf_name = serializers.CharField(source='etf.name', read_only=True)
    # ``signal`` is SET_NULL: a leg outlives the signal row that opened it, and the
    # FK id then reads as None while the date must not raise on the way out.
    signal_date = serializers.SerializerMethodField()

    def get_signal_date(self, obj):
        return obj.signal.date if obj.signal_id else None

    class Meta:
        model = ETFRotationPosition
        fields = [
            'id', 'signal', 'signal_date', 'etf', 'etf_ts_code', 'etf_name',
            'entry_date', 'exit_date', 'entry_price', 'exit_price', 'weight', 'shares',
            'pnl', 'return_pct', 'status', 'metadata', 'created_at',
        ]
        read_only_fields = fields


class ETFRotationBacktestSerializer(serializers.ModelSerializer):
    """Detail representation: metrics plus the curve, ledger and runtime state."""

    class Meta:
        model = ETFRotationBacktest
        fields = [
            'id', 'name', 'status', 'start_date', 'end_date', 'rebalance_frequency',
            'max_positions', 'momentum_lookback', 'defensive_asset',
            'absolute_momentum_filter', 'initial_capital', 'commission_rate',
            'min_commission', 'slippage', 'liquidity_floor_multiple', 'model_type',
            'walk_forward_steps', 'benchmark_code',
            'total_return', 'annualized_return', 'max_drawdown', 'sharpe_ratio',
            'calmar_ratio', 'win_rate', 'turnover_annual', 'alpha_vs_benchmark',
            'beta_vs_benchmark', 'information_ratio', 'benchmark_return', 'total_fees',
            'equity_curve', 'trade_log', 'parameters', 'report', 'error_message',
            'started_at', 'completed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ETFRotationBacktestListSerializer(ETFRotationBacktestSerializer):
    """Grid runs produce hundreds of rows; no list page may serialise their curves."""

    class Meta(ETFRotationBacktestSerializer.Meta):
        fields = [
            f for f in ETFRotationBacktestSerializer.Meta.fields
            if f not in {'equity_curve', 'trade_log', 'report'}
        ]
