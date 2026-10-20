from rest_framework import mixins, permissions, viewsets

from .models import ChangelogEntry
from .serializers import ChangelogEntrySerializer


class ChangelogEntryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Public read-only API changelog.

    Returns all changelog entries sorted by release date (newest first).
    Filterable by ``?version=1.1.0`` or ``?is_breaking=true``.
    """

    queryset = ChangelogEntry.objects.all()
    serializer_class = ChangelogEntrySerializer
    # Changelog is intentionally public (no authentication required)
    permission_classes = [permissions.AllowAny]
    filterset_fields = ['version', 'change_type', 'is_breaking']
    search_fields = ['title', 'description', 'endpoint']
