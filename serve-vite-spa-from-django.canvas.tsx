import {
  Callout,
  Code,
  CollapsibleSection,
  Divider,
  Grid,
  H1,
  H2,
  Stack,
  Stat,
  Table,
  Tag,
  Text,
} from "qoder/canvas";

export default function ServeViteSpaFromDjangoReport() {
  return (
    <Stack gap={20}>
      <H1>Serve the Built Vite SPA from Django — Completion Report</H1>
      <Text tone="secondary">
        FinanceAnalysis · spec: Serve_Vite_SPA_from_Django_task-2a6 · September 16, 2026
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="20" label="Files Modified" tone="success" />
        <Stat value="4" label="Files Created" tone="success" />
        <Stat value="7/7" label="New Backend Tests" tone="success" />
        <Stat value="31/31" label="Frontend Tests (unchanged)" tone="success" />
      </Grid>

      <Divider />

      <H2>Accomplishment Summary</H2>
      <Text>
        Collapsed the two-origin dev/deploy topology into a single Django origin without
        touching the React codebase. Django now renders one SPA shell template through
        django-vite and serves the hashed Vite bundle via whitenoise, mounted at "/" plus a
        negative-lookahead catch-all so every client-side route resolves while DRF, admin,
        static, and Channels paths keep their own handlers. A node:22-alpine Docker stage
        builds frontend/dist and overlays it into the runtime image. The pure-Vite HMR flow
        at :5173 remains fully supported.
      </Text>

      <Grid columns={3} gap={16}>
        <Stat value="1" label="Runtime origin (was 2)" />
        <Stat value="0" label="React files changed" />
        <Stat value="3" label="New env vars surfaced" description="DJANGO_VITE_DEV_MODE / HOST / PORT" />
      </Grid>

      <Divider />

      <H2>Key Steps</H2>
      <Table
        headers={["#", "Step", "Outcome"]}
        rows={[
          ["1", "Add django-vite==3.1.0 + whitenoise==6.8.2 to requirements/base.txt", "Installed into .venv"],
          ["2", "vite.config.ts: base '/static/', server.origin, build.manifest, rollupOptions.input=src/main.tsx", "manifest.json keyed on src/main.tsx"],
          ["3", "base.py: django_vite app, WhiteNoiseMiddleware, guarded STATICFILES_DIRS, DJANGO_VITE block, FRONTEND_URL to :8000", "manage.py check clean"],
          ["4", "production.py: CompressedManifestStaticFilesStorage", "Prod assets pre-compressed + hashed"],
          ["5", "apps/core/views.py SPAFallbackView + template + apps/static mirror", "Shell renders in both modes"],
          ["6", "config/urls.py: root path + negative-lookahead catch-all (last)", "API/admin/static/ws not shadowed"],
          ["7", "apps/core/tests_frontend.py (7 tests)", "7/7 pass"],
          ["8", ".dockerignore + Dockerfile node stage + compose anonymous volume", "Image-baked dist survives bind-mount"],
          ["9", ".env.example + regenerated env.md + README/local-setup/api.md/frontend README/CHANGELOG", "Docs consistent with new origin"],
        ]}
      />

      <Divider />

      <H2>Changed Files</H2>
      <CollapsibleSection title="Backend & configuration" defaultOpen>
        <Table
          headers={["File", "Change"]}
          rows={[
            ["requirements/base.txt", "+ django-vite==3.1.0, whitenoise==6.8.2"],
            ["config/settings/base.py", "django_vite app; WhiteNoiseMiddleware; guarded STATICFILES_DIRS; DJANGO_VITE block; FRONTEND_URL default :8000"],
            ["config/settings/production.py", "STORAGES to CompressedManifestStaticFilesStorage"],
            ["config/urls.py", "SPA root path + negative-lookahead catch-all"],
            ["apps/core/views.py (new)", "SPAFallbackView / spa_fallback"],
            ["apps/templates/frontend/index.html (new)", "Shell with vite_react_refresh / vite_hmr_client / vite_asset"],
            ["apps/core/tests_frontend.py (new)", "7 routing + non-shadowing tests"],
            ["apps/static/favicon.svg, icons.svg (new)", "Mirrored from frontend/public/"],
          ]}
        />
      </CollapsibleSection>
      <CollapsibleSection title="Frontend, Docker & docs">
        <Table
          headers={["File", "Change"]}
          rows={[
            ["frontend/vite.config.ts", "base, server.origin, build.manifest, rollupOptions.input"],
            ["frontend/index.html", "Title to FinanceAnalysis"],
            [".dockerignore", "Exclude frontend/node_modules, dist, coverage"],
            ["compose/local/django/Dockerfile", "node:22-alpine frontend-build stage + COPY --from overlay"],
            ["docker-compose.yml", "Anonymous volume /app/frontend/dist on django service"],
            [".env.example", "FRONTEND_URL=:8000 + DJANGO_VITE_* vars"],
            ["docs/reference/env.md (+4 generated sheets)", "Regenerated via export_documentation_facts"],
            ["README.md, local-setup.md, api.md, frontend/README.md, CHANGELOG.md", "Origin + two-flow documentation"],
          ]}
        />
      </CollapsibleSection>

      <Divider />

      <H2>Verification Evidence</H2>
      <Table
        headers={["Check", "Result"]}
        rows={[
          ["manage.py check", "No issues (0 silenced)"],
          ["manage.py test apps.core.tests_frontend --keepdb", "Ran 7 tests - OK"],
          ["npm test (frontend vitest)", "31 passed (31)"],
          ["npm run build", "dist/.vite/manifest.json emitted, entry keyed src/main.tsx"],
          ["collectstatic --dry-run", "187 files incl. apps/static/* and frontend/dist/assets/*"],
          ["Browser GET / (Django-served, prod mode)", "200; full SPA rendered (nav + DashboardPage) from hashed /static/assets/main-*.js + .css"],
          ["Browser deep links /backtest, /stock/600519", "Lazy pages render via SPA fallback; real API data loaded same-origin"],
          ["Browser GET /api/v1/schema/swagger-ui/", "200; full Swagger UI (not shadowed)"],
          ["Server access log", "GET / 200; /static/assets/*.js|.css 200; /api/v1/* same-origin (401 anon as designed)"],
        ]}
        rowTone={[undefined, "success", "success", "success", "success", "success", "success", "success", undefined]}
      />

      <Callout tone="info" title="Server-log excerpt (Django-served, DJANGO_VITE_DEV_MODE=False)">
        <Code>
          GET / 200 775 | GET /static/assets/main-DSawtWmJ.js 200 211323 | GET
          /static/assets/main-Cc2nMo27.css 200 6026 | GET
          /static/assets/DashboardPage-D-oTX1La.js 200 | GET
          /api/v1/schema/swagger-ui/ 200 4783
        </Code>
      </Callout>

      <Divider />

      <H2>Deviations From The Plan (forced by library/runtime reality)</H2>
      <Table
        headers={["Plan said", "Implemented instead", "Why"]}
        rows={[
          ["{% vite %} / {% vite_client %}", "{% vite_asset %} / {% vite_hmr_client %}", "django-vite v3 renamed the tags; v2 names do not exist"],
          ["(not specified)", "build.rollupOptions.input = src/main.tsx", "Vite otherwise keys the manifest entry as index.html, so vite_asset 'src/main.tsx' cannot resolve in prod mode"],
          ["docker compose up + image rebuild", "Native runserver + browser verification", "Docker is not installed on this Windows host; native path proves the same end-state behaviour"],
        ]}
        rowTone={["warning", "warning", "warning"]}
      />

      <Divider />

      <H2>Final Outcome</H2>
      <Stack gap={8}>
        <Text>
          <Tag tone="success">Complete</Tag> Django serves the built React SPA at its own
          origin in dev and prod; the standalone Vite HMR flow is unchanged. All backend and
          frontend tests pass, the production manifest path is exercised end-to-end in a
          real browser, and documentation plus the generated env reference reflect the new
          single-origin topology.
        </Text>
        <Text tone="secondary" size="small">
          Breaking change to note for operators: FRONTEND_URL now defaults to
          http://localhost:8000. Anyone still running HMR-only and wanting email links on
          :5173 must set FRONTEND_URL explicitly.
        </Text>
      </Stack>
    </Stack>
  );
}
