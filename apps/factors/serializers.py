from rest_framework import serializers

from .models import FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore


class FundamentalFactorSnapshotSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)

    class Meta:
        model = FundamentalFactorSnapshot
        fields = [
            'id', 'asset', 'asset_symbol', 'date',
            'pe', 'pb', 'roe', 'roe_qoq', 'metadata', 'created_at',
        ]


class CapitalFlowSnapshotSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)

    class Meta:
        model = CapitalFlowSnapshot
        fields = [
            'id', 'asset', 'asset_symbol', 'date',
            'northbound_net_5d', 'northbound_net_10d', 'northbound_net_20d',
            'main_force_net_5d', 'margin_balance_change_5d',
            'metadata', 'created_at',
        ]


class FactorScoreSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)

    class Meta:
        model = FactorScore
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name', 'date', 'mode',
            'fundamental_score', 'capital_flow_score', 'technical_score',
            'financial_weight', 'flow_weight', 'technical_weight',
            'composite_score', 'bottom_probability_score',
            'pe_percentile_score', 'pb_percentile_score', 'roe_trend_score',
            'northbound_flow_score', 'main_force_flow_score', 'margin_flow_score',
            'technical_reversal_score', 'metadata', 'created_at', 'updated_at',
        ]
