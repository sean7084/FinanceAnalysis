from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import ChangelogEntry


class DeveloperPortalPublicSurfaceTests(TestCase):
    """Phase 16 — public developer-portal surfaces (changelog + API docs)."""

    def setUp(self):
        self.client = APIClient()

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
