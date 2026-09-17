"""Tests for the SPA fallback view and the URLConf ordering that guards it.

Django's test runner forces ``DEBUG=False`` regardless of the developer's
``.env``. Combined with ``DJANGO_VITE_DEV_MODE`` defaulting to ``DEBUG`` in
``config/settings/base.py``, that would leave the tests in prod mode, which
requires ``frontend/dist/.vite/manifest.json`` to exist. CI machines and fresh
checkouts do not always have a built frontend, so these tests force the
django-vite singleton into dev mode for their duration -- dev mode never reads
the manifest, only emits URLs against ``http://localhost:5173``.

Overriding ``settings.DJANGO_VITE`` via ``override_settings`` would NOT work
here: ``DjangoViteAssetLoader.instance()`` is a singleton populated once at
``AppConfig.ready`` and does not listen for the ``setting_changed`` signal.
We swap the client directly instead.
"""

from django.test import TestCase
from django_vite.core.asset_loader import (
    DjangoViteAppClient,
    DjangoViteAssetLoader,
    DjangoViteConfig,
    DEFAULT_APP_NAME,
)


class SPAFallbackRoutingTests(TestCase):
    """Exercise the catch-all in ``config/urls.py`` end-to-end.

    ``TestCase`` (rather than ``SimpleTestCase``) is required because
    ``DATABASES['default']['ATOMIC_REQUESTS'] = True`` in
    ``config/settings/base.py`` wraps every HTTP request in a transaction,
    which touches the DB connection even for the SPA shell view. This matches
    the convention used across ``apps/*/tests.py``.
    """

    def setUp(self):
        loader = DjangoViteAssetLoader.instance()
        self._original_client = loader._apps.get(DEFAULT_APP_NAME)
        dev_config = DjangoViteConfig(
            dev_mode=True,
            dev_server_host="localhost",
            dev_server_port=5173,
        )
        loader._apps[DEFAULT_APP_NAME] = DjangoViteAppClient(dev_config, DEFAULT_APP_NAME)

    def tearDown(self):
        loader = DjangoViteAssetLoader.instance()
        if self._original_client is not None:
            loader._apps[DEFAULT_APP_NAME] = self._original_client

    def test_root_renders_spa_shell(self):
        """`GET /` returns the SPA shell with dev-mode Vite tags."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<div id="root"></div>', html)
        # dev_mode emits absolute URLs against the Vite dev server on :5173.
        self.assertIn("http://localhost:5173", html)
        self.assertIn("src/main.tsx", html)
        self.assertIn("@vite/client", html)

    def test_spa_fallback_serves_deep_link(self):
        """Deep links used in verification / password-reset emails render the shell.

        ``apps/users/views.py`` builds ``{FRONTEND_URL}/verify-email/<token>``
        and ``{FRONTEND_URL}/reset-password/<token>``; both must resolve
        through the catch-all so react-router-dom can pick them up client-side.
        """
        for path in ("/verify-email/some-token", "/reset-password/some-token", "/dashboard"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('<div id="root"></div>', response.content.decode())

    def test_spa_fallback_does_not_shadow_api(self):
        """`/api/v1/nonexistent/` must NOT render the SPA shell.

        The catch-all's negative lookahead excludes ``api/`` prefixes, so this
        path falls through to Django's default 404 handler rather than the
        SPA. Content type is not asserted because DRF's exception handler is
        only invoked when a viewset matches -- an unmatched /api/v1/... path
        is a plain Django 404 (text/html). The important invariant is that
        the SPA shell is never served for API paths.
        """
        response = self.client.get("/api/v1/nonexistent/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('<div id="root"></div>', response.content.decode())

    def test_spa_fallback_does_not_shadow_known_api_endpoint(self):
        """A real /api/v1/... route returns DRF's JSON body, never the SPA shell.

        Uses the OpenAPI schema endpoint, which requires no auth and no DB
        rows, so the test stays hermetic.
        """
        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")
        self.assertIn(response.status_code, (200, 401, 403, 406))
        self.assertNotIn('<div id="root"></div>', response.content.decode())

    def test_spa_fallback_does_not_shadow_admin(self):
        """`/admin/` must redirect to admin login, not render the SPA shell."""
        response = self.client.get("/admin/")
        self.assertIn(response.status_code, (301, 302))
        self.assertNotIn('<div id="root"></div>', response.content.decode())

    def test_spa_fallback_does_not_shadow_browsable_api_auth(self):
        """`/api-auth/login/` must reach DRF's session login view."""
        response = self.client.get("/api-auth/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<div id="root"></div>', response.content.decode())

    def test_spa_fallback_does_not_shadow_static(self):
        """`/static/...` must be handled by staticfiles / WhiteNoise, not the SPA.

        A missing static asset returns 404 (or 403 depending on the finder
        configuration); either way it must NOT render the SPA shell.
        """
        response = self.client.get("/static/definitely-missing-asset.js")
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn('<div id="root"></div>', response.content.decode())
