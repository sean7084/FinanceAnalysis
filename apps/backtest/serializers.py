from rest_framework import serializers

from .models import BacktestRun, BacktestTrade


class BacktestTradeSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)

    class Meta:
        model = BacktestTrade
        fields = [
            'id', 'backtest_run', 'asset', 'asset_symbol', 'asset_name',
            'trade_date', 'side', 'quantity', 'price', 'fee', 'slippage',
            'amount', 'pnl', 'signal_payload', 'metadata', 'created_at',
        ]


class BacktestRunSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    trades_count = serializers.IntegerField(source='trades.count', read_only=True)

    class Meta:
        model = BacktestRun
        fields = [
            'id', 'user', 'user_username', 'name', 'strategy_type', 'status',
            'start_date', 'end_date', 'initial_capital', 'cash', 'final_value',
            'total_return', 'annualized_return', 'max_drawdown', 'sharpe_ratio',
            'win_rate', 'total_trades', 'winning_trades', 'parameters', 'report',
            'error_message', 'started_at', 'completed_at', 'created_at', 'updated_at',
            'trades_count',
        ]
        read_only_fields = [
            'status', 'cash', 'final_value', 'total_return', 'annualized_return',
            'max_drawdown', 'sharpe_ratio', 'win_rate', 'total_trades',
            'winning_trades', 'report', 'error_message', 'started_at', 'completed_at',
            'created_at', 'updated_at', 'trades_count',
        ]

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError('start_date must be earlier than or equal to end_date.')
        return attrs
