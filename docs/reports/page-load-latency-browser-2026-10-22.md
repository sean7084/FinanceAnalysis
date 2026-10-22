# Browser time-to-data: all eight routes, 2026-10-22

The other report in this directory, `page-load-latency-cold-2026-10-22.md`, prices
pages with `curl` and therefore measures a server-side floor. The acceptance bar is
**browser time-to-data**, so the difference between the two -- bundle, route chunk,
React render and the paint after the last response -- had to be measured rather than
assumed. `FRONTEND_OVERHEAD_SECONDS` sat at `0.000s` for a release precisely because
assuming it is what let three pages of optimization look like a pass.

Host: WSL2, single developer machine, PostgreSQL and Redis on `192.168.31.8`, Chrome
on the Windows run through interop. Prod build (`npm run build` + `collectstatic`)
served by Django with `DJANGO_VITE_DEV_MODE=False`, never the dev server. Data as of
factor date **2026-10-20**, CSI 500 effective universe of **500** assets.

## Method, and what it deliberately is not

`scripts/measure_browser_time_to_data.sh` + `scripts/browser_time_to_data.mjs`.

No Playwright. The plan rejected it and the reason held: the cold API term was
minutes and the JS term is milliseconds, so a new devDependency plus a ~170 MB
Chromium download could not have changed a verdict. There is already a browser on
this host, so the probe uses it -- and the harness **exits rather than reporting an
assumed number** if it cannot find one.

Two constraints shaped it:

- Chrome's DevTools port binds the Windows loopback, which WSL cannot reach under NAT
  networking, so there is no CDP.
- `--virtual-time-budget` distorts the `performance.now()` deltas being measured, so
  it cannot be used to make headless Chrome wait.

Instead the proxy holds the load event open: an `async` subresource
(`/__hold?tk=…`) stays pending until the page POSTs its report, and `--dump-dom`
therefore exits on the stamp rather than on the shell. `async` matters -- a blocking
script in `<head>` would stall the parser and the app would never boot.

The observer reports by the first rule that fires, in order of trustworthiness:

| method | meaning |
| --- | --- |
| `stamp` | `data-tti` written by `frontend/src/hooks/useApiResource.ts`: navigationStart to the view opening, plus the opening to the last first value |
| `settle` | no `data-tti`, but every `/api/` request of the first burst has landed, nothing is in flight, and nothing has been asked for in a whole settle window |
| `shell` | a painted page with nothing in flight and no page-load request at all (Settings by design) |

`settle` and `shell` add the 750 ms window the probe actually waited, so a fallback
figure can be late but never early. Two guards keep that promise, because firing a
fallback early is the one way this probe could report a page *faster* than it is:

- **nothing may be in flight.** A page mid-request looks exactly like a page that has
  finished. The first warm run labelled `/indicator-board` as `shell` while its one
  request was still open, which would have been read as a 1070 ms pass for a page whose
  data had not arrived yet.
- **a fallback is only taken on a route that is not expected to stamp.** Every route is
  expected to stamp except Settings, so a fallback elsewhere means the app's own
  time-to-data went quiet -- and a fallback cannot tell that from a page whose first
  request had not started yet. Those routes wait for the deadline instead, the probe
  exits non-zero, and the wrapper retries the iteration once (resetting the stack first
  in cold mode, because an attempt that navigated before hanging left Redis warm).

So the tables below contain only stamped routes plus Settings' labelled shell figure,
and a route with no number is reported as **no measurement** and fails the run rather
than passing on a mixture of two probes.

## Cold

`COLD=1 ITERATIONS=2 LABEL=cold-head`,
`reports/browser_tti_20261022_180918_cold-head/`. Cold here is stricter than the API
report: `restart_cold_stack.sh` (Django, beat, all five worker pools) plus
`cache.clear()` plus a **brand-new Chrome `--user-data-dir` per iteration**, so
neither an HTTP cache nor `lib/queryCache.ts` survives. Sixteen probes, sixteen
usable: every route stamped, no fallback and no retry needed.

| route | page | browser total | how | open (bundle+chunk) | wait (data) | network span | browser term (max) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | Dashboard | 782 ms | stamp | 502 | 280 | 278 | 504 |
| `/indicator-board` | Indicator Board | 1652 ms | stamp | 538 | 1114 | 1112 | 540 |
| `/stock/000001` | Stock Detail | 984 ms | stamp | 454 | 530 | 529 | 486 |
| `/macro` | Macro Context | 535 ms | stamp | 483 | 52 | 51 | 484 |
| `/models` | Models | 570 ms | stamp | 475 | 95 | 93 | 477 |
| `/backtest` | Backtest | 794 ms | stamp | 549 | 245 | 242 | 552 |
| `/alerts` | Alert Center | 628 ms | stamp | 467 | 161 | 160 | 468 |
| `/settings` | Settings | 1058 ms | shell | -- | -- | -- | (shell) |

**Worst page 1652 ms against a 3000 ms budget -- PASS on all eight, on the first
request after a full stack restart.**

Read the row one sample at a time for the total, but the last column is the
largest browser-only term over *both* of the route's cold samples, because that is
the quantity the constant below takes. `/stock/000001` is the clearest case: its
worst total (984 ms) came with a 455 ms browser term, while the other sample --
892 ms total, 484 ms of it `open` -- shows what the route's browser cost can be.

This run supersedes `cold-final2` (worst page 1860 ms, constant 0.625 s) because it
measures the bundle at HEAD: `3776675` rebuilt the Stock Detail chunk. The shape of
the answer did not change -- eight PASS, one page an order of magnitude off the
others -- and the constant moved the way a smaller bundle moves.

## Warm

`COLD=0 ITERATIONS=2 LABEL=warm-head`,
`reports/browser_tti_20261022_183700_warm-head/`, same fresh Chrome profile per
route. Twenty-four probes, all usable.

| route | browser total | open | wait | network span | browser term (max) |
| --- | --- | --- | --- | --- | --- |
| `/` | 759 ms | 582 | 177 | 176 | 583 |
| `/indicator-board` | 501 ms | 446 | 55 | 54 | 447 |
| `/stock/000001` | 962 ms | 478 | 484 | 483 | 479 |
| `/macro` | 592 ms | 539 | 53 | 52 | 540 |
| `/models` | 638 ms | 541 | 97 | 96 | 542 |
| `/backtest` | 1371 ms | 425 | 946 | 943 | 475 |
| `/alerts` | 582 ms | 535 | 47 | 45 | 537 |
| `/settings` | 1042 ms | -- | -- | -- | (shell) |

Cold and warm differ where they are supposed to -- Indicator Board's own wait is
1114 ms cold against 55 ms warm -- and are the same within run-to-run noise
everywhere else. That is the finding: after the cold-path work, the "slow only on
first load" symptom has no browser-side residue to hide in. The exception worth
saying out loud is `/backtest`, whose second warm sample caught a 946 ms wait
against 245 ms cold: a warm Redis does not make every endpoint faster, and at two
samples one slow request moves the row. Every page's first contentful paint lands
between 156 and 328 ms (164-308 ms cold), so the shell is never the wait, and the
whole spread between pages is the API term.

## The constant this feeds

Largest browser-only term across the cold stamped routes: **0.552 s** (`/backtest`,
whose bundle and route chunk paint after a 245 ms wait). The warm pass reads
**0.583 s** on `/`. The figure charged is the larger of the two, because the column
exists to be charged to a page that has not been measured yet and should not be
beaten by one:

```bash
FRONTEND_OVERHEAD_SECONDS=0.583 COLD=1 ITERATIONS=3 bash scripts/measure_page_api_latency.sh
```

Charged as one constant rather than per route on purpose: the API harness already
reports each page's own critical path, and a single conservative browser term keeps
the two from double-billing the round trip (`data-tti` includes the request, so the
overhead is `data-tti-open` plus the part of `data-tti` that is not the network span).

## What these numbers do not say

- **Two cold iterations is a floor, not a percentile.** At n=2 the interpolated p95
  is the maximum, which the summary states rather than hides. The API harness ran
  three. A distribution would need ~20 full-stack restarts per page, which is a lot
  of machine and would still say the same thing at 1.65 s against 3.0 s.
- **The probe proxies the HTML.** Requests for the document and its assets pass
  through a one-page Node server on the way to Django, and Chrome runs on the Windows
  host while the files are served from WSL. That costs some of the ~500 ms `open`
  term, so `FRONTEND_OVERHEAD_SECONDS` is conservative for a same-host deployment and
  specific to this machine.
- **`open` is where the browser's own startup lands, and it is not always quiet.** An
  earlier pass lost two probes to a Chrome that started and never painted -- no
  request, no first paint, nothing to time. That is why the wrapper re-runs an
  iteration once and why a route with no number fails the run: on a shared developer
  machine the honest reading of "no data" is *not a measurement*, not a pass.
- **`/settings` is a shell figure, not a data figure.** It makes no page-load request;
  its 1058 ms is first paint plus the settle window the probe waited in, so
  the true value is nearer that paint. It is the one route allowed to be measured this
  way, and it is labelled in every table.
- **Stock Detail's queued-generation refresh is not in these numbers.** The poll fires
  at 5 s and the probe exits on the first stamp, so no cold or warm figure here pays for
  it. What it does mean is that the browser pass proves the *first* answer while the
  refresh itself is covered by `frontend/src/pages/StockDetailPage.test.tsx`, which
  asserts the poll runs, fills the rows in, skips an `unavailable` queue and stops after
  twelve ticks.
- **Stock Detail's burst is measured, its chained re-render is not.** `data-tti` is
  stamped when the last resource's value is committed to React state, which is a frame
  before that frame paints. That is the same instant on every route, so it is
  consistent, but it is not a pixel.
- **Nothing here proves the pages are *correct*.** Fast and empty is a failure mode:
  the read-stored-rows contract depends on prediction coverage, which is still partial
  (see `prediction_coverage` in the cold report). A page can now answer in 300 ms with
  fewer rows than it should have.

## Reproduce

```bash
COLD=1 ITERATIONS=2 LABEL=cold bash scripts/measure_browser_time_to_data.sh
COLD=0 ITERATIONS=3 LABEL=warm bash scripts/measure_browser_time_to_data.sh
FULL_STACK=0 COLD=1 bash scripts/measure_browser_time_to_data.sh   # Redis-only reset
```

Guards that keep it true: `frontend/src/hooks/useApiResource.test.tsx` (the stamp,
its decomposition and `trackViewResource`) and
`apps/core/tests_frontend.py::PageLoadLatencyHarnessTests`, which fails if
`scripts/measure_browser_time_to_data.sh` covers fewer routes than `App.tsx` has or if
a page that reads the API stops registering with the instrument -- the hole that left
Stock Detail and Backtest with no measurement at all.
