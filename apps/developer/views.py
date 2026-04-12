from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import ChangelogEntry, DeveloperAPIKey
from .serializers import (
    ChangelogEntrySerializer,
    DeveloperAPIKeyCreateSerializer,
    DeveloperAPIKeySerializer,
)


class DeveloperAPIKeyViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Developer API key management.

    list:   List all active keys owned by the authenticated user.
    create: Mint a new key (raw key returned once).
    retrieve: Retrieve key metadata (no raw key).
    destroy: Revoke a key (sets ``is_active=False``).

    Custom actions
    ~~~~~~~~~~~~~~
    POST /{id}/rotate/ — Revoke the current key and mint a replacement.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DeveloperAPIKeySerializer

    def get_queryset(self):
        return DeveloperAPIKey.objects.filter(user=self.request.user)

    # Override create so we can use the dedicated input serializer
    def create(self, request, *args, **kwargs):
        in_ser = DeveloperAPIKeyCreateSerializer(data=request.data)
        in_ser.is_valid(raise_exception=True)
        data = in_ser.validated_data

        instance, raw_key = DeveloperAPIKey.generate(
            user=request.user,
            name=data['name'],
            is_sandbox=data.get('is_sandbox', False),
            expires_at=data.get('expires_at'),
        )

        out_ser = DeveloperAPIKeySerializer(instance)
        response_data = dict(out_ser.data)
        response_data['raw_key'] = raw_key  # inject once
        return Response(response_data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """Soft-delete: mark as inactive rather than deleting the row."""
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='rotate')
    def rotate(self, request, pk=None):
        """
        Revoke the existing key and issue a new one with the same settings.
        Returns the new raw key (displayed once).
        """
        old = self.get_object()
        old.is_active = False
        old.save(update_fields=['is_active'])

        new_instance, raw_key = DeveloperAPIKey.generate(
            user=request.user,
            name=old.name,
            is_sandbox=old.is_sandbox,
            expires_at=old.expires_at,
        )

        out_ser = DeveloperAPIKeySerializer(new_instance)
        response_data = dict(out_ser.data)
        response_data['raw_key'] = raw_key
        return Response(response_data, status=status.HTTP_201_CREATED)


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
