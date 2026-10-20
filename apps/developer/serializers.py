from rest_framework import serializers

from .models import ChangelogEntry


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
