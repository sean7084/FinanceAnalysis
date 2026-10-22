#!/usr/bin/env node
// Real-browser time-to-data for the eight SPA routes.
//
// Why this exists: scripts/measure_page_api_latency.sh can only charge the API
// term, so FRONTEND_OVERHEAD_SECONDS sat at 0.000s. The plan's own stand-in for
// Playwright is "load each route in a fresh window against the prod build and
// read data-tti", and there is a browser here -- Chrome on the Windows host,
// reached through WSL interop -- so the reading can actually be taken.
//
// Chrome's DevTools port binds the Windows loopback, which WSL cannot reach in
// NAT networking, and --virtual-time-budget corrupts the very performance.now()
// deltas being measured. So instead of CDP this runs a one-page proxy on the WSL
// side, injects an observer, and holds an `async` subresource open so the load
// event -- and therefore headless Chrome's --dump-dom exit -- waits for the
// stamp instead of dumping the shell. Every number is real browser time.
//
// Usage: node scripts/browser_time_to_data.mjs --route /macro --label cold --json
// Env:   TT_UPSTREAM (http://127.0.0.1:8001), TT_PORT (8002), TT_CHROME,
//        TT_PROFILE (Windows-style temp dir, fresh per run), TT_DEADLINE_MS.

import { spawn } from 'node:child_process'
import { createServer, request as httpRequest } from 'node:http'

const args = new Map()
for (let i = 2; i < process.argv.length; i += 1) {
  if (!process.argv[i].startsWith('--')) continue
  const value = process.argv[i + 1]
  args.set(process.argv[i].slice(2), value && !value.startsWith('--') ? value : 'true')
}
const ROUTE = args.get('route') || '/'
const LABEL = args.get('label') || 'run'
const UPSTREAM = new URL(process.env.TT_UPSTREAM || 'http://127.0.0.1:8001')
const PORT = Number(process.env.TT_PORT || 8002)
const DEADLINE_MS = Number(process.env.TT_DEADLINE_MS || 30000)
// How long a page must look quiet before the probe calls its first batch of data
// "arrived". Also the addition made to a settle/shell total, which is why a
// fallback figure can be late but never early.
const SETTLE_MS = Number(process.env.TT_SETTLE_MS || 750)
// Whether this route is expected to have an app-side time-to-data stamp. Only
// Settings is not: it asks for nothing at load, so there is no resource for the
// app to time and the shell figures are the honest reading. Every other route that
// lands on a fallback is a probe failure, not a number.
const EXPECTS_STAMP = (process.env.TT_EXPECT_STAMP || '1') !== '0'
const TOKEN = `tti${Date.now().toString(36)}`
const CHROME =
  process.env.TT_CHROME || '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe'
const PROFILE =
  process.env.TT_PROFILE || `C:\\Users\\sean_\\AppData\\Local\\Temp\\qoder-tti-${process.pid}`

// Watched by the proxy, reported by the page. Inlined into HTML, so it stays
// ES5-with-var: no build step and no source map to keep in sync.
const OBSERVER = String.raw`
(function () {
  var deadline = Date.now() + __TTI_DEADLINE__;
  var settleMs = __TTI_SETTLE_MS__;
  function apiTiming() {
    var e = performance.getEntriesByType('resource')
      .filter(function (r) { return r.name.indexOf('/api/') > -1; })
      .map(function (r) {
        return {
          name: r.name.replace(/^https?:\/\/[^/]+/, ''),
          start: Math.round(r.startTime),
          responseEnd: Math.round(r.responseEnd),
          duration: Math.round(r.duration),
        };
      })
      .sort(function (a, b) { return a.responseEnd - b.responseEnd; });
    // Time-to-data is when the page's *first* batch of data has landed. A route
    // that keeps refreshing -- /backtest re-asks for its trades every 10s -- would
    // otherwise report its next poll as first load, so the burst ends at the first
    // gap wider than the settle window.
    var burstEnd = e.length ? e[e.length - 1].responseEnd : null;
    for (var i = e.length - 1; i > 0; i--) {
      if (e[i].responseEnd - e[i - 1].responseEnd > settleMs) burstEnd = e[i - 1].responseEnd;
      else break;
    }
    return {
      entries: e,
      firstStart: e.length ? Math.min.apply(null, e.map(function (r) { return r.start; })) : null,
      burstEnd: burstEnd,
    };
  }
  var token = window.__TTI_TOKEN || '';
  var inflight = {};
  var nativeFetch = window.fetch;
  // Wrapped so a route that never stamps can say *which* request is still open;
  // without this a no-stamp result is indistinguishable from a broken probe.
  window.fetch = function (input) {
    var url = typeof input === 'string' ? input : (input && input.url) || String(input);
    inflight[url] = (inflight[url] || 0) + 1;
    return nativeFetch.apply(window, arguments).then(
      function (r) { inflight[url] -= 1; return r; },
      function (e) { inflight[url] -= 1; throw e; }
    );
  };
  function openRequests() {
    return Object.keys(inflight).filter(function (k) { return inflight[k] > 0; });
  }
  function fcpEntry() {
    return performance.getEntriesByType('paint').filter(function (e) {
      return e.name === 'first-contentful-paint';
    })[0];
  }
  function snapshot(method, quietMs) {
    var ds = document.body.dataset;
    var t = apiTiming();
    var fcp = fcpEntry();
    return {
      token: token,
      path: location.pathname,
      method: method,
      settleMs: quietMs,
      ttiOpen: ds.ttiOpen ? Number(ds.ttiOpen) : null,
      tti: ds.tti ? Number(ds.tti) : null,
      firstContentfulPaint: fcp ? Math.round(fcp.startTime) : null,
      firstRequestStart: t.firstStart,
      burstEnd: t.burstEnd,
      openRequests: openRequests(),
      apiRequests: t.entries,
    };
  }
  var finished = false;
  function report(method) {
    if (finished) return;
    finished = true;
    try {
      fetch('/__tti', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(snapshot(method, settleMs)),
      });
    } catch (e) { /* the proxy is gone; there is nobody left to tell */ }
  }
  // Three ways to know the page has its data, in order of trustworthiness:
  //
  //   stamp   -- hooks/useApiResource.ts wrote data-tti. The app's own statement
  //              that its last first value is committed.
  //   settle  -- every /api/ request of the first burst has landed and nothing has
  //              been asked for in a whole settle window. A page that asks for
  //              data and lands here is not registered with the instrument --
  //              which is how Stock Detail and Backtest went unmeasurable -- so
  //              the record keeps the method and the summary says so.
  //   shell   -- a painted page with no page-load request at all (Settings).
  //
  // Both fallback totals add the settle window, so they can only overstate the
  // wait, never understate it -- the bias a time-to-data probe must not have.
  // Two guards keep that promise, because a fallback that fires early is the one
  // way this probe could report a page faster than it is:
  //
  //   nothing open   a page mid-request looks identical to one that has finished;
  //                  the warm run caught /indicator-board being called "shell"
  //                  while its one request was still in flight.
  //   not expected to stamp   a route that does ask for data has an app-side stamp,
  //                  so a fallback there means the stamp never came -- which is a
  //                  measurement failure to chase, not a number to pass on. Those
  //                  routes wait for the deadline instead.
  var timer = setInterval(function () {
    var elapsed = performance.now();
    if (document.body && document.body.dataset.ttiPath) {
      clearInterval(timer);
      report('stamp');
      return;
    }
    var t = apiTiming();
    var quiet = openRequests().length === 0;
    if (__TT_EXPECT_STAMP__ === 0) {
      if (t.burstEnd !== null && quiet && elapsed - t.burstEnd > settleMs) {
        clearInterval(timer);
        report('settle');
        return;
      }
      var fcp = fcpEntry();
      if (!t.entries.length && quiet && fcp && elapsed - fcp.startTime > settleMs * 2) {
        clearInterval(timer);
        report('shell');
        return;
      }
    }
    if (Date.now() > deadline) {
      clearInterval(timer);
      report('deadline');
    }
  }, 20);
})();
`

const held = new Map()
let resolveRun
const run = new Promise((resolve) => { resolveRun = resolve })

function proxy(pathname, search, res) {
  const req = httpRequest(
    {
      host: UPSTREAM.hostname,
      port: UPSTREAM.port,
      path: `${pathname}${search || ''}`,
      method: 'GET',
      headers: { 'accept-encoding': 'identity', 'user-agent': 'qoder-tti-probe' },
    },
    (up) => {
      const chunks = []
      up.on('data', (c) => chunks.push(c))
      up.on('end', () => {
        const body = Buffer.concat(chunks)
        const type = up.headers['content-type'] || ''
        const headers = {}
        for (const key of Object.keys(up.headers)) {
          if (!['content-length', 'transfer-encoding', 'content-encoding'].includes(key)) {
            headers[key] = up.headers[key]
          }
        }
        if (type.includes('text/html')) {
          const injected =
            `<script>window.__TTI_TOKEN='${TOKEN}';</script>` +
            `<script>${OBSERVER.replaceAll('__TTI_DEADLINE__', String(DEADLINE_MS)).replaceAll(
              '__TTI_SETTLE_MS__',
              String(SETTLE_MS),
            ).replaceAll('__TT_EXPECT_STAMP__', EXPECTS_STAMP ? '1' : '0')}</script>` +
            `<script async src="/__hold?tk=${TOKEN}"></script>`
          res.writeHead(up.statusCode, headers)
          res.end(body.toString('utf8').replace('</head>', `${injected}</head>`))
        } else {
          res.writeHead(up.statusCode, headers)
          res.end(body)
        }
      })
    },
  )
  req.on('error', (e) => {
    res.writeHead(502, { 'content-type': 'text/plain' })
    res.end(`upstream error: ${e.message}`)
  })
  req.end()
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`)

  if (url.pathname === '/__hold') {
    // Pending until the page reports, which is what keeps the load event -- and
    // so Chrome's exit -- open. `async` matters: a blocking script in the head
    // would stall the parser and the app would never boot at all.
    res.writeHead(200, { 'content-type': 'application/javascript', 'cache-control': 'no-store' })
    held.set(url.searchParams.get('tk'), res)
    return
  }

  if (url.pathname === '/__tti' && req.method === 'POST') {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      let payload
      try {
        payload = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      } catch {
        payload = { path: ROUTE, error: 'unparseable report' }
      }
      const stream = held.get(payload.token)
      if (stream) {
        held.delete(payload.token)
        stream.end('/* released */')
      }
      res.writeHead(204)
      res.end()
      resolveRun(payload)
    })
    return
  }

  proxy(url.pathname, url.search, res)
})

server.listen(PORT, '0.0.0.0')

const chrome = spawn(
  CHROME,
  [
    '--headless',
    '--disable-gpu',
    '--no-sandbox',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    `--user-data-dir=${PROFILE}`,
    `--timeout=${DEADLINE_MS + 15000}`,
    '--dump-dom',
    `http://127.0.0.1:${PORT}${ROUTE}`,
  ],
  { stdio: ['ignore', 'ignore', 'ignore'] },
)
// The wrapper refuses to run without a discoverable browser, but this script can
// be called directly, and an unhandled 'error' event on a ChildProcess throws out
// of the event loop rather than reporting anything useful.
chrome.once('error', (e) => {
  resolveRun({ path: ROUTE, method: 'deadline', error: `cannot start ${CHROME}: ${e.message}` })
})

const outcome = await Promise.race([
  run,
  new Promise((resolve) => {
    setTimeout(() => resolve({ path: ROUTE, error: `no report within ${DEADLINE_MS}ms` }), DEADLINE_MS + 8000)
  }),
])

// Chrome normally exits on its own the moment the load event fires. If it has
// not, close only the instance this script started: killing chrome.exe by image
// name would take the user's own browser with it.
if (chrome.exitCode === null) {
  await new Promise((resolve) => {
    chrome.once('exit', resolve)
    chrome.kill('SIGTERM')
    setTimeout(resolve, 5000)
  })
}
server.close()

const settleMs = typeof outcome.settleMs === 'number' ? outcome.settleMs : SETTLE_MS
// Absolute time from navigationStart to the page having its data, by whichever
// method the observer could use:
//
//   stamp    the app's own data-tti, i.e. navigationStart plus the view open time
//            plus the wait for its last first value.
//   settle   the last response of the first burst, plus the settle window the
//            probe had to wait to be sure nothing else was coming.
//   shell    first paint plus that same window, for a page that asks for nothing.
//
// Both fallbacks add the window they actually waited, so they can report a page
// slower than it is but never faster -- the direction a budget probe has to lean.
// They are only taken on a route that is not expected to stamp (see the observer):
// there, a fallback figure would be an approximation whose direction cannot be
// proved, because a page whose first request starts after the quiet window looks
// exactly like one that asks for nothing.
const method = outcome.method || 'deadline'
let browserTotalMs = null
if (outcome.method === 'stamp' && typeof outcome.ttiOpen === 'number' && typeof outcome.tti === 'number') {
  browserTotalMs = outcome.ttiOpen + outcome.tti
} else if (outcome.method === 'settle' && typeof outcome.burstEnd === 'number') {
  browserTotalMs = outcome.burstEnd + settleMs
} else if (outcome.method === 'shell' && typeof outcome.firstContentfulPaint === 'number') {
  browserTotalMs = outcome.firstContentfulPaint + settleMs
}
// Resource timings are offsets from navigationStart, so the network term is the
// span from the first request leaving to the last of the first batch landing. The
// page fires its calls concurrently, which makes that span what it waits on.
const apiWaitMs =
  typeof outcome.burstEnd === 'number' && typeof outcome.firstRequestStart === 'number'
    ? outcome.burstEnd - outcome.firstRequestStart
    : null

// Every route is expected to stamp now that the two hand-composed pages register
// with the instrument, so a route that must stamp and did not has no usable number.
// Dropping it is what makes the wrapper retry the probe instead of passing a page on
// a reading the app never made -- which is how the Dashboard group went stale.
const unprovable = EXPECTS_STAMP && browserTotalMs !== null && method !== 'stamp'
if (unprovable) browserTotalMs = null

const record = {
  label: LABEL,
  route: ROUTE,
  method,
  stamped: method === 'stamp',
  browserTotalMs,
  openMs: outcome.ttiOpen ?? null,
  waitMs: outcome.tti ?? null,
  burstEndMs: outcome.burstEnd ?? null,
  firstRequestStartMs: outcome.firstRequestStart ?? null,
  apiWaitMs,
  // The term the API harness cannot see: bundle, route chunk and the paint after
  // the last response. browserTotalMs is the SLO quantity itself; this is what
  // measure_page_api_latency.sh charges on top of its own curl p95.
  overheadMs:
    browserTotalMs !== null && apiWaitMs !== null ? browserTotalMs - apiWaitMs : browserTotalMs,
  firstContentfulPaintMs: outcome.firstContentfulPaint ?? null,
  apiRequests: outcome.apiRequests || [],
  openRequests: outcome.openRequests || [],
  error:
    outcome.error ||
    (unprovable
      ? `expected the app's own stamp on ${ROUTE}, got ${method}`
      : method === 'deadline'
        ? `no report within ${DEADLINE_MS}ms; still open: ${(outcome.openRequests || []).join(', ') || 'none'}`
        : null),
  // Not an error, but the harness should be able to see it: this is the one route
  // whose budget figure is the prod shell rather than the app's measurement.
  note:
    !EXPECTS_STAMP && (method === 'settle' || method === 'shell')
      ? `measured by ${method}, not the app's stamp`
      : null,
}

process.stdout.write(`${JSON.stringify(record, null, args.get('json') ? 0 : 2)}\n`)
// A record with no number is a failure, whatever it says in error: the wrapper
// logs it, retries once, and a non-zero exit is what stops a run being read as a pass.
process.exit(browserTotalMs === null ? 1 : 0)
