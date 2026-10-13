import {
  Callout,
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

export default function CookieRefreshAuthUpgradeReport() {
  return (
    <Stack gap={20}>
      <H1>Cookie Refresh Auth Upgrade — Completion Report</H1>
      <Text tone="secondary">
        FinanceAnalysis · spec: Cookie_Refresh_Auth_Upgrade_task-27e · October 13, 2026
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="124" label="Backend tests pass" tone="success" description="incl. 11 new CookieAuthTests" />
        <Stat value="35" label="Frontend tests pass" tone="success" description="incl. 8 rewritten auth tests" />
        <Stat value="11" label="Files changed" tone="success" description="5 backend · 6 frontend" />
        <Stat value="0" label="Lint / type errors" tone="success" description="tsc + eslint clean" />
      </Grid>

      <Divider />

      <H2>Accomplishment Summary</H2>
      <Text>
        Migrated the FinanceAnalysis SPA authentication from a localStorage-based JWT refresh flow to
        an HttpOnly cookie-based refresh with in-memory access tokens. Fixed three root causes of
        frequent re-authentication: (1) 60-min access TTL with no proactive refresh, (2) cross-tab
        refresh race against token blacklist rotation, (3) JS-readable refresh token vulnerable to
        XSS. New model: 8h access token in JS memory, 30-day sliding refresh in HttpOnly SameSite=Lax
        cookie, proactive refresh timer with jitter, BroadcastChannel cross-tab mutex.
      </Text>

      <H2>Session Policy Change</H2>
      <Table
        headers={["Setting", "Before", "After"]}
        rows={[
          ["ACCESS_TOKEN_LIFETIME", "60 minutes", "8 hours"],
          ["REFRESH_TOKEN_LIFETIME", "7 days", "30 days (sliding via rotation)"],
          ["Refresh transport", "localStorage (JS-readable)", "HttpOnly cookie fa_refresh"],
          ["Access transport", "localStorage", "JS memory only"],
          ["Cross-tab coordination", "none (race -> blacklist wipe)", "BroadcastChannel('fa-auth') mutex"],
          ["Proactive refresh", "none (reactive 401 only)", "Timer at exp-120s + jitter + visibilitychange"],
          ["Auth throttle", "100/day", "500/day"],
        ]}
      />

      <Divider />

      <H2>Key Steps</H2>
      <Table
        headers={["#", "Phase", "Action", "Outcome"]}
        rows={[
          ["1", "Backend settings", "Retuned SIMPLE_JWT (8h/30d), added AUTH_COOKIE_* block, raised auth throttle", "config/settings/base.py"],
          ["2", "Backend views", "Added CookieTokenObtainPairView, CookieTokenRefreshView, CookieTokenLogoutView, SessionStateView + cookie helpers", "apps/users/views.py"],
          ["3", "Backend URLs", "Wired /auth/login/, /auth/refresh/, /auth/logout/, /auth/session/; preserved legacy /auth/token/", "config/urls.py"],
          ["4", "Env docs", "Documented AUTH_COOKIE_NAME/SECURE/SAMESITE/DOMAIN knobs", ".env.example"],
          ["5", "Frontend api.ts", "In-memory accessToken, cookie refresh, loginWithCredentials/logout/bootstrapSession, proactive timer, BroadcastChannel mutex, credentials:'include'", "frontend/src/lib/api.ts"],
          ["6", "Frontend boot", "bootstrapSession() resolves before first render", "frontend/src/main.tsx"],
          ["7", "Frontend UI", "Switched to loginWithCredentials, removed refresh-token field, added Sign out button", "AuthSettingsPanel.tsx"],
          ["8", "Frontend i18n", "Updated authHelp/persistence strings (en+zh), added settings.signOut", "frontend/src/i18n.tsx"],
          ["9", "Vite config", "Added cookie proxy contract comment", "frontend/vite.config.ts"],
          ["10", "Backend tests", "11 new CookieAuthTests: login cookie, rotation+blacklist, logout, session probe, legacy regression", "apps/users/tests.py"],
          ["11", "Frontend tests", "8 rewritten auth tests incl. BroadcastChannel signed-out assertion", "frontend/src/lib/api.test.ts"],
        ]}
      />

      <Divider />

      <H2>Changed Files</H2>
      <CollapsibleSection title="Backend (5 files)" defaultOpen>
        <Table
          headers={["File", "Change"]}
          rows={[
            ["config/settings/base.py", "SIMPLE_JWT 8h/30d, AUTH_COOKIE_* block, auth throttle 500/day"],
            ["apps/users/views.py", "4 new cookie views + 3 helpers (_cookie_samesite, _set/_delete_refresh_cookie)"],
            ["config/urls.py", "4 new routes (login/refresh/logout/session), legacy token routes kept"],
            [".env.example", "AUTH_COOKIE_NAME/SECURE/SAMESITE/DOMAIN documented with deployment notes"],
            ["apps/users/tests.py", "11 new CookieAuthTests covering full cookie lifecycle"],
          ]}
        />
      </CollapsibleSection>
      <CollapsibleSection title="Frontend (6 files)" defaultOpen>
        <Table
          headers={["File", "Change"]}
          rows={[
            ["frontend/src/lib/api.ts", "In-memory access, cookie refresh, proactive timer, BroadcastChannel, bootstrapSession/login/logout; removed obtainJwtToken*/readRefreshToken/JWT_REFRESH_KEY/persistJwtTokens/clearStoredJwtTokens"],
            ["frontend/src/main.tsx", "bootstrapSession() gates first render"],
            ["frontend/src/components/layout/AuthSettingsPanel.tsx", "loginWithCredentials, removed refresh field, added Sign out"],
            ["frontend/src/i18n.tsx", "authHelp/persistence updated (en+zh), settings.signOut added"],
            ["frontend/vite.config.ts", "Cookie proxy contract comment"],
            ["frontend/src/lib/api.test.ts", "8 tests rewritten for cookie flow + BroadcastChannel mock"],
          ]}
        />
      </CollapsibleSection>

      <Divider />

      <H2>Verification Evidence</H2>
      <Table
        headers={["Check", "Result"]}
        rows={[
          ["Backend tests (apps.users/core/developer/analytics)", "124 pass, 0 fail"],
          ["New CookieAuthTests", "11 pass (login cookie, rotation, blacklist, logout, session, legacy)"],
          ["Frontend tests (vitest)", "35 pass across 4 files"],
          ["Frontend auth tests", "8 pass incl. BroadcastChannel signed-out broadcast assertion"],
          ["TypeScript (tsc --noEmit)", "clean, 0 errors"],
          ["ESLint", "clean, 0 errors"],
          ["Vite production build", "succeeds in 403ms"],
          ["Removed symbols grep", "obtainJwtToken, obtainJwtTokenPair, readRefreshToken, JWT_REFRESH_KEY, persistJwtTokens, clearStoredJwtTokens all absent"],
        ]}
        rowTone={["success", "success", "success", "success", "success", "success", "success", "success"]}
      />

      <Divider />

      <H2>Final Outcome</H2>
      <Text>
        <Tag tone="success">Complete</Tag> All spec requirements implemented and verified. Users get:
        one re-login after deploy (legacy token cleanup on bootstrap), then no more mid-day logouts
        (8h access + silent cookie refresh + proactive timer), multi-tab safety (BroadcastChannel
        mutex eliminates the blacklist rotation race), a 30-day sliding session (each refresh
        re-issues a full 30-day cookie), and improved security (refresh token never readable by JS).
      </Text>

      <Callout tone="warning" title="Deployment note">
        <Text size="small">
          Production behind a TLS-terminating proxy must ensure Django sees X-Forwarded-Proto: https
          (or set SECURE_PROXY_SSL_HEADER) so AUTH_COOKIE_SECURE (defaults to not DEBUG) emits Secure
          cookies correctly. SameSite=Lax is sufficient for the current same-origin deployment; if the
          SPA ever moves to a different origin, upgrade to SameSite=None + Secure=True and add
          django-cors-headers.
        </Text>
      </Callout>

      <Callout tone="info" title="Architecture decision">
        <Text size="small">
          Access token stays in JS memory (not a cookie) so the WebSocket ?token= contract in
          apps/analytics/consumers.py is unchanged and the API stays REST-friendly. The refresh token
          in an HttpOnly cookie is the standard "BFF-lite" pattern: the browser holds the long-lived
          credential, JS holds only the short-lived one.
        </Text>
      </Callout>

      <Text tone="secondary" size="small">
        Generated for the Cookie_Refresh_Auth_Upgrade_task-27e goal · every claim verified against
        test output, tsc, eslint, and vite build.
      </Text>
    </Stack>
  );
}
