from rest_framework import serializers

from .models import ChangelogEntry, DeveloperAPIKey


class DeveloperAPIKeySerializer(serializers.ModelSerializer):
    """
    Read serializer for API keys.  Never exposes the hash or full key.
    The ``raw_key`` field (write-once) is injected by the view on creation.
    """

    username = serializers.CharField(source='user.username', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    raw_key = serializers.CharField(
        read_only=True,
        help_text='Full API key — returned ONCE on creation only.',
    )

    class Meta:
        model = DeveloperAPIKey
        fields = [
            'id',
            'name',
            'key_prefix',
            'username',
            'is_active',
            'is_sandbox',
            'is_expired',
            'created_at',
            'last_used_at',
            'expires_at',
            'raw_key',
        ]
        read_only_fields = [
            'id', 'key_prefix', 'username', 'is_active',
            'is_expired', 'created_at', 'last_used_at', 'raw_key',
        ]


class DeveloperAPIKeyCreateSerializer(serializers.Serializer):
    """Input-only serializer for key creation."""

    name = serializers.CharField(max_length=100)
    is_sandbox = serializers.BooleanField(default=False)
    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        default=None,
        help_text='ISO-8601 datetime.  Leave null for non-expiring keys.',
    )


class ChangelogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangelogEntry
        fields = [
            'id',
            'version',
            'release_date',
            'change_type',
            'title',
            'description',
            'is_breaking',
            'endpoint',
            'created_at',
        ]
        read_only_fields = fields
