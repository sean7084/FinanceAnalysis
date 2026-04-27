from rest_framework import serializers

from .models import BacktestRun, BacktestTrade


VALID_ENTRY_WEEKDAYS = {'MON', 'TUE', 'WED', 'THU', 'FRI'}
VALID_CANDIDATE_MODES = {'top_n', 'trade_score'}
VALID_TRADE_SCORE_SCOPES = {'independent', 'combined'}
VALID_TOP_N_METRICS = {'trade_score', 'up_prob_3d', 'up_prob_7d', 'up_prob_30d'}
TOP_N_METRIC_HORIZON_MAP = {
    'up_prob_3d': 3,
    'up_prob_7d': 7,
    'up_prob_30d': 30,
}


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

        strategy_type = attrs.get('strategy_type') or getattr(self.instance, 'strategy_type', None)
        parameters = dict(attrs.get('parameters') or getattr(self.instance, 'parameters', {}) or {})

        if strategy_type == BacktestRun.StrategyType.PREDICTION_THRESHOLD:
            prediction_source = str(parameters.get('prediction_source', 'heuristic')).lower()
            if prediction_source not in {'heuristic', 'lightgbm', 'lstm'}:
                raise serializers.ValidationError({'parameters': 'prediction_source must be one of heuristic, lightgbm, or lstm.'})

            missing = [key for key in ['top_n', 'horizon_days', 'up_threshold'] if key not in parameters]
            if missing:
                raise serializers.ValidationError({'parameters': f'missing required keys for prediction threshold: {", ".join(missing)}'})

            try:
                top_n = int(parameters['top_n'])
                horizon_days = int(parameters['horizon_days'])
                up_threshold = float(parameters['up_threshold'])
            except (TypeError, ValueError):
                raise serializers.ValidationError({'parameters': 'top_n and horizon_days must be integers; up_threshold must be numeric.'})

            if top_n <= 0:
                raise serializers.ValidationError({'parameters': 'top_n must be greater than 0.'})
            if horizon_days not in {3, 7, 30}:
                raise serializers.ValidationError({'parameters': 'horizon_days must be one of 3, 7, or 30.'})
            if not 0 <= up_threshold <= 1:
                raise serializers.ValidationError({'parameters': 'up_threshold must be between 0 and 1.'})

            if 'holding_period_days' in parameters:
                try:
                    holding_period_days = int(parameters['holding_period_days'])
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'parameters': 'holding_period_days must be an integer.'})
                if holding_period_days <= 0:
                    raise serializers.ValidationError({'parameters': 'holding_period_days must be greater than 0.'})

            if 'capital_fraction_per_entry' in parameters:
                try:
                    capital_fraction_per_entry = float(parameters['capital_fraction_per_entry'])
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'parameters': 'capital_fraction_per_entry must be numeric.'})
                if not 0 < capital_fraction_per_entry <= 1:
                    raise serializers.ValidationError({'parameters': 'capital_fraction_per_entry must be between 0 and 1.'})

            candidate_mode = str(parameters.get('candidate_mode', 'top_n')).lower()
            if candidate_mode not in VALID_CANDIDATE_MODES:
                raise serializers.ValidationError({'parameters': 'candidate_mode must be either top_n or trade_score.'})
            parameters['candidate_mode'] = candidate_mode

            top_n_metric = str(parameters.get('top_n_metric') or '').lower()
            if not top_n_metric:
                top_n_metric = {
                    3: 'up_prob_3d',
                    7: 'up_prob_7d',
                    30: 'up_prob_30d',
                }.get(horizon_days, 'up_prob_7d')

            if top_n_metric not in VALID_TOP_N_METRICS:
                raise serializers.ValidationError({'parameters': 'top_n_metric must be one of trade_score, up_prob_3d, up_prob_7d, or up_prob_30d.'})

            aligned_horizon = TOP_N_METRIC_HORIZON_MAP.get(top_n_metric)
            if aligned_horizon is not None:
                parameters['horizon_days'] = aligned_horizon
                horizon_days = aligned_horizon
            parameters['top_n_metric'] = top_n_metric

            if 'max_positions' in parameters:
                try:
                    max_positions = int(parameters['max_positions'])
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'parameters': 'max_positions must be an integer.'})
                if max_positions <= 0:
                    raise serializers.ValidationError({'parameters': 'max_positions must be greater than 0.'})

            if 'trade_score_threshold' in parameters:
                try:
                    float(parameters['trade_score_threshold'])
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'parameters': 'trade_score_threshold must be numeric.'})

            trade_score_scope = str(parameters.get('trade_score_scope', 'independent')).lower()
            if trade_score_scope not in VALID_TRADE_SCORE_SCOPES:
                raise serializers.ValidationError({'parameters': 'trade_score_scope must be either independent or combined.'})
            parameters['trade_score_scope'] = trade_score_scope

            for boolean_key in ['use_macro_context', 'enable_stop_target_exit']:
                if boolean_key in parameters and not isinstance(parameters[boolean_key], bool):
                    raise serializers.ValidationError({'parameters': f'{boolean_key} must be a boolean.'})

            if 'trade_decision_policy' in parameters:
                policy = parameters['trade_decision_policy']
                if not isinstance(policy, dict):
                    raise serializers.ValidationError({'parameters': 'trade_decision_policy must be an object.'})

                normalized_policy = dict(policy)
                if 'include_near_round_target' in normalized_policy and not isinstance(normalized_policy['include_near_round_target'], bool):
                    raise serializers.ValidationError({'parameters': 'trade_decision_policy.include_near_round_target must be a boolean.'})

                for key in ['min_target_return_pct', 'min_stop_distance_pct']:
                    if key not in normalized_policy:
                        continue
                    try:
                        value = float(normalized_policy[key])
                    except (TypeError, ValueError):
                        raise serializers.ValidationError({'parameters': f'trade_decision_policy.{key} must be numeric.'})
                    if not 0 <= value <= 0.5:
                        raise serializers.ValidationError({'parameters': f'trade_decision_policy.{key} must be between 0 and 0.5.'})

                parameters['trade_decision_policy'] = normalized_policy

            if 'entry_weekdays' in parameters:
                raw_weekdays = parameters['entry_weekdays']
                if isinstance(raw_weekdays, str):
                    weekday_values = [part.strip().upper() for part in raw_weekdays.split(',') if part.strip()]
                elif isinstance(raw_weekdays, list):
                    weekday_values = [str(part).strip().upper() for part in raw_weekdays if str(part).strip()]
                else:
                    raise serializers.ValidationError({'parameters': 'entry_weekdays must be a list or comma-separated string.'})

                invalid = [value for value in weekday_values if value[:3] not in VALID_ENTRY_WEEKDAYS]
                if invalid:
                    raise serializers.ValidationError({'parameters': f'unsupported entry_weekdays values: {", ".join(invalid)}'})
                parameters['entry_weekdays'] = [value[:3] for value in weekday_values]

            parameters['prediction_source'] = prediction_source
            attrs['parameters'] = parameters

        return attrs
