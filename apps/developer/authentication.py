import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import DeveloperAPIKey


class APIKeyAuthentication(BaseAuthentication):
    """
    Authenticate requests using an ``X-API-Key`` header.

    Usage::

        curl -H "X-API-Key: fa-<your-key>" https://api.example.com/api/v1/assets/

    The raw key is hashed (SHA-256) and looked up against the stored hash.
    Only active, non-expired keys are accepted.
    """

    HEADER = 'HTTP_X_API_KEY'

    def authenticate(self, request):
        raw_key = request.META.get(self.HEADER, '').strip()
        if not raw_key:
            return None  # Let other authenticators try

        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

        try:
            api_key = (
                DeveloperAPIKey.objects
                .select_related('user')
                .get(key_hash=key_hash, is_active=True)
            )
        except DeveloperAPIKey.DoesNotExist:
            raise AuthenticationFailed('Invalid or revoked API key.')

        if api_key.is_expired:
            raise AuthenticationFailed('API key has expired.')

        # Non-blocking last-used timestamp update
        DeveloperAPIKey.objects.filter(pk=api_key.pk).update(
            last_used_at=timezone.now()
        )

        # Return (user, auth) tuple — api_key doubles as the auth object
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return 'X-API-Key'
