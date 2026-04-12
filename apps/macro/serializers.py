from rest_framework import serializers

from .models import MacroSnapshot, MarketContext, EventImpactStat


class MacroSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = MacroSnapshot
        fields = [
            'id', 'date', 'dxy', 'cny_usd', 'cn10y_yield', 'cn2y_yield',
            'pmi_manufacturing', 'pmi_non_manufacturing', 'cpi_yoy', 'ppi_yoy',
            'metadata', 'created_at', 'updated_at',
        ]


class MarketContextSerializer(serializers.ModelSerializer):
    macro_phase_display = serializers.CharField(source='get_macro_phase_display', read_only=True)

    class Meta:
        model = MarketContext
        fields = [
            'id', 'context_key', 'macro_phase', 'macro_phase_display', 'event_tag',
            'is_active', 'starts_at', 'ends_at', 'notes', 'metadata',
            'created_at', 'updated_at',
        ]


class EventImpactStatSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventImpactStat
        fields = [
            'id', 'event_tag', 'sector', 'horizon_days', 'avg_return', 'excess_return',
            'sample_size', 'observations_start', 'observations_end', 'metadata', 'created_at',
        ]
