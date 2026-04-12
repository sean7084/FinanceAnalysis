import hashlib

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import ChangelogEntry, DeveloperAPIKey


class Phase16DeveloperPortalTests(TestCase):
    """Phase 16 — API Documentation & Developer Portal test suite."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase16_user',
            email='phase16@example.com',
            password='Passw0rd!123',
        )
        self.other_user = User.objects.create_user(
            username='phase16_other',
            email='phase16other@example.com',
            password='Passw0rd!456',
        )

    # ------------------------------------------------------------------
    # Authentication guard
    # ------------------------------------------------------------------

    def test_key_list_requires_auth(self):
        """GET /api/v1/developer/keys/ must return 401 for anonymous requests."""
        response = self.client.get('/api/v1/developer/keys/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_key_create_requires_auth(self):
        """POST /api/v1/developer/keys/ must return 401 for anonymous requests."""
        response = self.client.post('/api/v1/developer/keys/', data={'name': 'test'}, format='json')
        # DRF returns 403 when DEFAULT_PERMISSION_CLASSES uses IsAuthenticatedOrReadOnly for list
        # but IsAuthenticated on this viewset → always 401
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    # ------------------------------------------------------------------
    # Key creation
    # ------------------------------------------------------------------

    def test_create_api_key_returns_raw_key_once(self):
        """POST /api/v1/developer/keys/ returns raw_key on creation."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            '/api/v1/developer/keys/',
            data={'name': 'CI pipeline', 'is_sandbox': False},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn('raw_key', data)
        raw_key = data['raw_key']
        self.assertTrue(raw_key.startswith('fa-'))
        self.assertEqual(len(raw_key), 43)
        # raw_key must not be retrievable again via list
        list_resp = self.client.get('/api/v1/developer/keys/')
        self.assertNotIn('raw_key', list_resp.json()['results'][0])

    def test_create_sandbox_api_key(self):
        """Creating a sandbox key sets is_sandbox=True."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            '/api/v1/developer/keys/',
            data={'name': 'Sandbox', 'is_sandbox': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.json()['is_sandbox'])

    # ------------------------------------------------------------------
    # API key authentication
    # ------------------------------------------------------------------

    def test_api_key_authentication_grants_access(self):
        """Requests authenticated via X-API-Key header should succeed."""
        instance, raw_key = DeveloperAPIKey.generate(user=self.user, name='Test Key')
        self.client.credentials(HTTP_X_API_KEY=raw_key)
        # Access a protected endpoint
        response = self.client.get('/api/v1/developer/keys/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_api_key_returns_401(self):
        """An invalid or tampered key triggers a 401."""
        self.client.credentials(HTTP_X_API_KEY='fa-notarealkey000000000000000000000000000000')
        response = self.client.get('/api/v1/developer/keys/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ------------------------------------------------------------------
    # Key revocation & rotation
    # ------------------------------------------------------------------

    def test_revoke_api_key_blocks_access(self):
        """Deleting (revoking) a key prevents further authentication with it."""
        instance, raw_key = DeveloperAPIKey.generate(user=self.user, name='Revoke Me')

        # Revoke the key
        self.client.force_authenticate(user=self.user)
        response = self.client.delete(f'/api/v1/developer/keys/{instance.pk}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Key should now be inactive
        instance.refresh_from_db()
        self.assertFalse(instance.is_active)

        # Clear force_authenticate before testing the raw key
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_X_API_KEY=raw_key)
        response = self.client.get('/api/v1/developer/keys/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rotate_api_key_issues_new_key(self):
        """POST /{id}/rotate/ deactivates old key and returns a new raw_key."""
        instance, raw_key = DeveloperAPIKey.generate(user=self.user, name='Rotate Me')
        self.client.force_authenticate(user=self.user)

        response = self.client.post(f'/api/v1/developer/keys/{instance.pk}/rotate/')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        data = response.json()
        self.assertIn('raw_key', data)
        new_raw_key = data['raw_key']
        self.assertNotEqual(new_raw_key, raw_key)
        self.assertTrue(new_raw_key.startswith('fa-'))

        # Old key should be inactive
        instance.refresh_from_db()
        self.assertFalse(instance.is_active)

        # New key should authenticate
        self.client.credentials(HTTP_X_API_KEY=new_raw_key)
        response = self.client.get('/api/v1/developer/keys/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Key isolation (users cannot access other users' keys)
    # ------------------------------------------------------------------

    def test_user_cannot_access_other_users_keys(self):
        """A user's key list is scoped to their own keys only."""
        DeveloperAPIKey.generate(user=self.user, name='User A key')
        # Login as other user; their list should be empty
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get('/api/v1/developer/keys/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['count'], 0)

    # ------------------------------------------------------------------
    # Changelog
    # ------------------------------------------------------------------

    def test_changelog_endpoint_is_public(self):
        """GET /api/v1/developer/changelog/ is accessible without authentication."""
        ChangelogEntry.objects.create(
            version='1.1.0',
            release_date='2026-04-12',
            change_type=ChangelogEntry.ChangeType.ADDED,
            title='Phase 15: Backtesting Engine',
            is_breaking=False,
        )
        response = self.client.get('/api/v1/developer/changelog/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['count'], 1)

    def test_changelog_breaking_filter(self):
        """?is_breaking=true filters correctly."""
        ChangelogEntry.objects.create(
            version='1.0.0', release_date='2026-01-01',
            change_type=ChangelogEntry.ChangeType.CHANGED,
            title='Auth change', is_breaking=True,
        )
        ChangelogEntry.objects.create(
            version='1.1.0', release_date='2026-04-12',
            change_type=ChangelogEntry.ChangeType.ADDED,
            title='Backtest API', is_breaking=False,
        )
        response = self.client.get('/api/v1/developer/changelog/?is_breaking=true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['count'], 1)
        self.assertTrue(response.json()['results'][0]['is_breaking'])

    # ------------------------------------------------------------------
    # OpenAPI schema endpoint
    # ------------------------------------------------------------------

    def test_openapi_schema_endpoint_accessible(self):
        """GET /api/v1/schema/ returns an OpenAPI 3.0 JSON document."""
        response = self.client.get('/api/v1/schema/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_swagger_ui_endpoint_accessible(self):
        """GET /api/v1/schema/swagger-ui/ returns Swagger UI HTML."""
        response = self.client.get('/api/v1/schema/swagger-ui/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b'swagger', response.content.lower())

    def test_redoc_endpoint_accessible(self):
        """GET /api/v1/schema/redoc/ returns ReDoc HTML."""
        response = self.client.get('/api/v1/schema/redoc/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b'redoc', response.content.lower())

    # ------------------------------------------------------------------
    # Model internals
    # ------------------------------------------------------------------

    def test_key_hash_is_sha256_of_raw_key(self):
        """The stored key_hash must be the SHA-256 hex digest of the raw key."""
        instance, raw_key = DeveloperAPIKey.generate(user=self.user, name='Hash Check')
        expected_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        self.assertEqual(instance.key_hash, expected_hash)

    def test_key_prefix_matches_raw_key(self):
        """key_prefix must be the first 12 characters of the raw key."""
        instance, raw_key = DeveloperAPIKey.generate(user=self.user, name='Prefix Check')
        self.assertEqual(instance.key_prefix, raw_key[:12])
