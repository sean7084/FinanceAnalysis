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

import re
from pathlib import Path

from django.test import SimpleTestCase, TestCase
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
        """Client-side routes resolve through the catch-all and render the shell.

        The SPA's react-router-dom handles ``/dashboard`` on the client, so the
        catch-all in ``config/urls.py`` must serve the shell rather than 404.
        """
        for path in ("/dashboard",):
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

    def test_spa_fallback_does_not_shadow_static(self):
        """`/static/...` must be handled by staticfiles / WhiteNoise, not the SPA.

        A missing static asset returns 404 (or 403 depending on the finder
        configuration); either way it must NOT render the SPA shell.
        """
        response = self.client.get("/static/definitely-missing-asset.js")
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn('<div id="root"></div>', response.content.decode())


class PageLoadLatencyHarnessTests(SimpleTestCase):
    """Keep scripts/measure_page_api_latency.sh a true map of the SPA.

    The harness is the only evidence behind the page-load budget, and it went
    stale for a whole release without anything noticing: it kept timing the
    seven calls ``fetchDashboardDataLegacy`` makes after ``fetchDashboardData``
    had moved to a single ``/dashboard/stocks/summary/``, so every Dashboard
    number described a request no page ever sends. Three of the eight routed
    pages were never measured at all.

    These tests derive the required coverage from the frontend source rather
    than restating it, so the check cannot itself go stale.
    """

    FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
    HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "measure_page_api_latency.sh"
    BROWSER_HARNESS = (
        Path(__file__).resolve().parents[2] / "scripts" / "measure_browser_time_to_data.sh"
    )

    # The ten routed pages, per the router in frontend/src/App.tsx.
    EXPECTED_GROUPS = {
        "Dashboard",
        "Indicator Board",
        "Stock Detail",
        "Macro Context",
        "Models",
        "Backtest",
        "ETF",
        "ETF Workbench",
        "Alert Center",
        "Settings",
    }

    def _page_files(self):
        return sorted((self.FRONTEND / "pages").glob("*Page.tsx"))

    def _harness_groups_and_routes(self):
        text = self.HARNESS.read_text()
        lines = text.splitlines()

        # Endpoint entries live in the array literal and in `ENDPOINTS+=` lines.
        # Matching is line-shaped rather than paren-depth for two reasons: the
        # comments inside the array carry unbalanced parentheses, and the
        # ONLY_PAGES filter reassigns `ENDPOINTS=("${filtered[@]}")`, which would
        # otherwise reopen the block and let the markdown table's pipes match as
        # entries. A real entry is a quoted triple whose third field is a path.
        entry_lines = []
        inside_array = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("ENDPOINTS=(\"$"):
                # Reassignment from the filtered copy, not a definition.
                continue
            if stripped.startswith("ENDPOINTS=("):
                inside_array = True
                continue
            if inside_array:
                if stripped == ")":
                    inside_array = False
                    continue
                if stripped.startswith("#"):
                    continue
                entry_lines.append(stripped)
            elif stripped.startswith("ENDPOINTS+=("):
                entry_lines.append(stripped)

        groups, routes = set(), set()
        for line in entry_lines:
            match = re.search(r"""['"]([^'"]*\|[^'"]*\|[^'"]+)['"]""", line)
            if not match:
                continue
            group, _label, path = match.group(1).split("|", 2)
            if not group or not path.startswith(("/", "$")):
                continue
            groups.add(group)
            first = path.lstrip("/").split("?")[0].split("/")[0]
            if first and not first.startswith("$"):
                routes.add(first)

        # Paths held in shell variables: the entry text is "$DASHBOARD_STOCKS",
        # so the route has to come from the assignment the array interpolates.
        for var in ("DASHBOARD_STOCKS", "OHLCV_BASE"):
            assignment = re.search(rf'^{var}="/([^/]+)/', text, re.MULTILINE)
            referenced = re.search(rf"\|\${var}\b", text)
            if assignment and referenced:
                routes.add(assignment.group(1))
        if "/dashboard/stocks/summary/" in text:
            routes.add("dashboard")
        return groups, routes

    def _spa_read_routes(self):
        """Routes the routed pages actually read, resolved through api.ts.

        Derived from what the pages import and call, so a helper no page uses
        cannot put a route on the load path. That is what excludes
        ``fetchDashboardDataLegacy`` without naming it.
        """
        api_source = (self.FRONTEND / "lib" / "api.ts").read_text()
        pages_source = "\n".join(path.read_text() for path in self._page_files())

        called = set(re.findall(r"\b(fetch[A-Z][A-Za-z0-9]*)\s*\(", pages_source))
        paths = set(re.findall(r"""apiGet(?:<[^>]*>)?\(\s*[`'"](/[^`'"?#]+)""", pages_source))

        for name in called:
            match = re.search(rf"export async function {name}\(", api_source)
            if not match:
                continue
            body = api_source[match.end() :]
            next_export = body.find("\nexport ")
            body = body[: next_export if next_export != -1 else len(body)]
            paths.update(re.findall(r"""apiGet(?:<[^>]*>)?\(\s*[`'"](/[^`'"]+)""", body))
            paths.update(re.findall(r"`\$\{API_BASE\}(/[^`$]*)", body))
            paths.update(re.findall(r"""[`'"](/[^`'"]*?/)\$\{""", body))

        routes = set()
        for path in paths:
            first = path.lstrip("/").split("/")[0]
            if first:
                routes.add(first)
        return routes

    def test_every_route_a_page_reads_is_measured(self):
        """A page-load read that the harness skips makes its cost invisible."""
        missing = self._spa_read_routes() - self._harness_groups_and_routes()[1]
        self.assertEqual(
            missing,
            set(),
            f"SPA reads {sorted(missing)} on a page-load path but the harness "
            "never times it. Add it to ENDPOINTS in scripts/measure_page_api_latency.sh.",
        )

    def test_harness_has_a_group_for_every_routed_page(self):
        """Every route in App.tsx needs a page group, including ones with no API call."""
        app_source = (self.FRONTEND / "App.tsx").read_text()
        # Every lazy page is wrapped by withSuspense; the parent AppShell route is
        # not, so counting wrappers counts pages rather than URL entries.
        route_count = len(re.findall(r"withSuspense\(<\w+ />", app_source))
        groups = self._harness_groups_and_routes()[0]
        self.assertEqual(
            groups,
            self.EXPECTED_GROUPS,
            f"harness groups {sorted(groups)} do not match the expected page set",
        )
        self.assertEqual(
            len(groups),
            route_count,
            "App.tsx renders a different number of pages than the harness groups "
            f"({route_count} vs {len(groups)}); a page was added or removed without "
            "updating the latency harness.",
        )

    def test_dashboard_measures_the_summary_endpoint_not_the_legacy_seven(self):
        """The specific regression that motivated this class.

        ``fetchDashboardData`` is one request to ``/dashboard/stocks/summary/``.
        If the harness stops measuring it, or resumes measuring a legacy-only
        endpoint, the Dashboard row describes a page load that does not happen.
        """
        _groups, routes = self._harness_groups_and_routes()
        harness = self.HARNESS.read_text()
        self.assertIn("dashboard", routes)
        self.assertIn("/dashboard/stocks/summary/", harness)
        self.assertNotIn(
            "Dashboard|macro current",
            harness,
            "the legacy seven-call Dashboard group was reintroduced; fetchDashboardData "
            "makes one summary request, so these endpoints are not on the load path",
        )

    def test_browser_harness_loads_every_routed_page(self):
        """The browser pass is the only evidence behind the overhead term.

        ``FRONTEND_OVERHEAD_SECONDS`` sat at 0.000s for a release because nothing
        read the app's own stamps. scripts/measure_browser_time_to_data.sh reads
        them; this keeps it from quietly covering fewer routes than the SPA has,
        which is the same blind spot from the other side.
        """
        app_source = (self.FRONTEND / "App.tsx").read_text()
        route_count = len(re.findall(r"withSuspense\(<\w+ />", app_source))
        app_paths = set(re.findall(r"path: '([^']*)'", app_source))

        declaration = re.search(
            r"^ROUTES=\((.*)\)$", self.BROWSER_HARNESS.read_text(), re.MULTILINE
        )
        self.assertIsNotNone(
            declaration,
            "the browser harness lost its ROUTES array, so it no longer says which "
            "pages it loads",
        )
        routes = re.findall(r'"([^"]+)"', declaration.group(1))
        self.assertEqual(len(routes), route_count)
        self.assertEqual(len(set(routes)), route_count, "a route is measured twice")

        for path in app_paths:
            # 'stock/:symbol' is measured against a symbol read from the API, so
            # the harness holds a variable rather than the literal segment.
            stripped = path.lstrip("/")
            prefix = f"/{stripped.split(':')[0]}" if stripped else "/"
            self.assertTrue(
                any(route.startswith(prefix) for route in routes),
                f"App.tsx routes '{path}' but the browser harness never loads {prefix}",
            )

    def test_every_page_that_reads_the_api_registers_with_the_instrument(self):
        """A route the app cannot time is a route the 3s bar cannot verify.

        ``data-tti`` is written by ``useApiResource``, or by ``trackViewResource``
        for the two pages that compose their requests by hand. Stock Detail and
        Backtest used neither, so the browser pass had no number for them and the
        two heaviest routes were unpriceable -- the same class of hole as an
        endpoint the curl harness skips, one layer up. A comment naming the hook
        is not a call, so the match is on the call sites.
        """
        uninstrumented = []
        for page in self._page_files():
            source = page.read_text()
            reads_api = "from '../lib/api'" in source or "cachedGet(" in source
            registers = (
                "useApiResource(" in source
                or "useApiResource<" in source
                or "trackViewResource(" in source
            )
            if reads_api and not registers:
                uninstrumented.append(page.name)
        self.assertEqual(
            uninstrumented,
            [],
            f"{uninstrumented} load page data through the API but never register with "
            "frontend/src/hooks/useApiResource.ts, so no time-to-data is recorded for "
            "them and scripts/measure_browser_time_to_data.sh has nothing to price",
        )
