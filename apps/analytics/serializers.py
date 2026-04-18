from rest_framework import serializers
from .models import TechnicalIndicator, ScreenerTemplate, AlertRule, AlertEvent, SignalEvent


class TechnicalIndicatorSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    
    class Meta:
        model = TechnicalIndicator
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name',
            'timestamp', 'indicator_type', 'value', 'parameters'
        ]


class TechnicalIndicatorListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views"""
    
    class Meta:
        model = TechnicalIndicator
        fields = ['indicator_type', 'value', 'timestamp']


class DashboardStockRowSerializer(serializers.Serializer):
    asset_id = serializers.IntegerField()
    asset_symbol = serializers.CharField()
    asset_name = serializers.CharField()
    date = serializers.DateField()
    composite_score = serializers.DecimalField(max_digits=7, decimal_places=6)
    bottom_probability_score = serializers.DecimalField(max_digits=7, decimal_places=6)
    fundamental_score = serializers.DecimalField(max_digits=7, decimal_places=6)
    capital_flow_score = serializers.DecimalField(max_digits=7, decimal_places=6)
    technical_score = serializers.DecimalField(max_digits=7, decimal_places=6)
    factor_sentiment_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    pe_percentile_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    pb_percentile_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    roe_trend_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    northbound_flow_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    main_force_flow_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    margin_flow_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    technical_reversal_score = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    current_close = serializers.DecimalField(max_digits=18, decimal_places=4, allow_null=True)
    sentiment_score = serializers.DecimalField(max_digits=8, decimal_places=6, allow_null=True)
    sentiment_label = serializers.CharField(allow_blank=True)
    rsi = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)
    macd = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)
    bb_upper = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)
    bb_lower = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)
    sma_60 = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)
    heuristic_label = serializers.CharField(allow_blank=True)
    heuristic_up_probability = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    heuristic_confidence = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    heuristic_trade_score = serializers.DecimalField(max_digits=12, decimal_places=6, allow_null=True)
    heuristic_target_price = serializers.DecimalField(max_digits=12, decimal_places=4, allow_null=True)
    heuristic_stop_loss_price = serializers.DecimalField(max_digits=12, decimal_places=4, allow_null=True)
    heuristic_risk_reward_ratio = serializers.DecimalField(max_digits=12, decimal_places=6, allow_null=True)
    heuristic_suggested = serializers.BooleanField()
    lightgbm_label = serializers.CharField(allow_blank=True)
    lightgbm_up_probability = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    lightgbm_confidence = serializers.DecimalField(max_digits=7, decimal_places=6, allow_null=True)
    lightgbm_trade_score = serializers.DecimalField(max_digits=12, decimal_places=6, allow_null=True)
    lightgbm_target_price = serializers.DecimalField(max_digits=12, decimal_places=4, allow_null=True)
    lightgbm_stop_loss_price = serializers.DecimalField(max_digits=12, decimal_places=4, allow_null=True)
    lightgbm_risk_reward_ratio = serializers.DecimalField(max_digits=12, decimal_places=6, allow_null=True)
    lightgbm_suggested = serializers.BooleanField()


class ScreenerTemplateSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner.username', read_only=True)

    class Meta:
        model = ScreenerTemplate
        fields = [
            'id', 'owner', 'owner_username', 'name', 'description',
            'screener_type', 'config', 'is_public', 'created_at', 'updated_at',
        ]
        read_only_fields = ['owner', 'created_at', 'updated_at']


class AlertRuleSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)

    class Meta:
        model = AlertRule
        fields = [
            'id', 'owner', 'owner_username', 'asset', 'asset_symbol', 'asset_name',
            'name', 'condition_type', 'indicator_type', 'threshold', 'custom_condition',
            'channels', 'cooldown_minutes', 'is_active', 'last_triggered_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['owner', 'last_triggered_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        condition_type = attrs.get('condition_type', getattr(self.instance, 'condition_type', None))
        indicator_type = attrs.get('indicator_type', getattr(self.instance, 'indicator_type', ''))
        channels = attrs.get('channels', getattr(self.instance, 'channels', []))

        indicator_conditions = {
            AlertRule.ConditionType.INDICATOR_ABOVE,
            AlertRule.ConditionType.INDICATOR_BELOW,
        }
        if condition_type in indicator_conditions and not indicator_type:
            raise serializers.ValidationError({'indicator_type': 'indicator_type is required for indicator alerts.'})

        allowed_channels = {'email', 'sms', 'websocket'}
        invalid_channels = [c for c in channels if c not in allowed_channels]
        if invalid_channels:
            raise serializers.ValidationError({'channels': f'Invalid channels: {invalid_channels}'})

        return attrs


class AlertEventSerializer(serializers.ModelSerializer):
    alert_name = serializers.CharField(source='alert_rule.name', read_only=True)
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)

    class Meta:
        model = AlertEvent
        fields = [
            'id', 'alert_rule', 'alert_name', 'asset', 'asset_symbol', 'status',
            'trigger_value', 'message', 'metadata', 'dispatched_channels', 'notified_at',
            'created_at',
        ]


class SignalEventSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    signal_type_display = serializers.CharField(source='get_signal_type_display', read_only=True)

    class Meta:
        model = SignalEvent
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name',
            'signal_type', 'signal_type_display',
            'timestamp', 'description', 'metadata', 'created_at',
        ]
