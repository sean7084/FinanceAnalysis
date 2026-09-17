# Create GitHub issues from BACKLOG.md triage
# 15 issues with labels and priority assignments

$token = (Get-Content '.env' | Select-String '^GITHUB_PERSONAL_ACCESS_TOKEN=').ToString().Split('=', 2)[1].Trim()
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github.v3+json"
}
$baseUrl = "https://api.github.com/repos/sean7084/FinanceAnalysis"

$issues = @(
    @{
        title = "Production code inspects whether it is being mocked (backtest/tasks.py)"
        labels = @("bug", "P2 - Medium", "component: backtest")
        body = @"
## Description
``apps/backtest/tasks.py`` around line 757 calls ``inspect.signature`` on ``_predict_lightgbm_for_asset.side_effect`` to decide whether to pass ``run=``. Production code branching on the shape of a test double is a compatibility shim for older stub signatures, and it hides drift instead of surfacing it.

The LSTM path has no such shim, which is why ``test_dashboard_stocks_overlays_runtime_lstm_candidate_payload`` failed loudly with ``TypeError`` -- the better failure mode.

## Proposed Fix
Remove the LightGBM shim and update every LightGBM stub in ``apps/backtest/tests.py`` to accept ``run=``.

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "apps/macro/tests.py depends on a gitignored local fixture"
        labels = @("bug", "P2 - Medium", "component: macro")
        body = @"
## Description
``test_backfill_macro_snapshots_uses_yahoo_cny_usd_before_tushare_start`` overrides ``MACRO_CNYUSD_CSV_PATH`` with a temp file, but ``backfill_macro_snapshots`` also loads the ChinaBond yield CSV from ``source_data/CGBYieldCurve_2010to2016.csv``. That directory is gitignored, so on a fresh clone the test fails with:

``````
CommandError: Historical yield CSV not found: .../source_data/CGBYieldCurve_2010to2016.csv
``````

## Proposed Fix
Build the yield fixture in a temp directory the way the CNY/USD case in the same module already does.

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "Non-hermetic sentiment tests make real network calls"
        labels = @("bug", "P2 - Medium", "component: sentiment")
        body = @"
## Description
Two tests in ``apps.sentiment.tests.Phase13SentimentTests`` make real network calls rather than using test doubles:

- ``test_sentiment_latest_endpoint``
- ``test_recalculate_endpoint_queues_pipeline``

Observed provider traffic during a test run: ``Total fetched: 1 items from providers: eastmoney``.

They currently fail on Redis first, which masks this defect. Once the credential is fixed they will still vary run to run with provider availability and quota.

## Proposed Fix
Add a ``locmem`` cache override and mock the providers.

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "Implausible model accuracies in the registry (likely look-ahead leakage)"
        labels = @("bug", "P1 - High", "component: ml-pipeline")
        body = @"
## Description
``ModelVersion`` rows for ``lgb-3d-2026-04-11`` and ``lgb-7d-2026-04-11`` record accuracies of **0.949667** and **0.882889**. Directional accuracy for this problem sits around 0.42-0.58 across every other family. These are almost certainly look-ahead leakage, not good models.

Worse, ``EnsembleWeightSnapshot`` id 1 carries a basis ``lightgbm_accuracy`` of **0.916278**, so the leaked artifacts propagated into ensemble weights. That snapshot is inactive now, but the mechanism is not guarded.

## Action Items
- [ ] Investigate what the 2026-04-11 feature pipeline did differently
- [ ] Consider retiring those rows to ``ARCHIVED`` so they cannot be selected
- [ ] Add a sanity bound that refuses to activate an artifact whose accuracy exceeds a plausible ceiling

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "Stuck RUNNING backtest runs need a sweep"
        labels = @("bug", "P2 - Medium", "component: backtest")
        body = @"
## Description
The coverage sheet shows runs in ``RUNNING`` whose ``created_at`` is weeks old. They will not self-heal. ``apps/backtest/task_health.py`` can identify them; see ``docs/how-to/runbook-sync-failure.md`` section 6.

## Action Items
- [ ] Run a sweep to identify all stuck runs
- [ ] Decide whether to restart or fail each one
- [ ] Consider adding automatic stale-run detection to Celery Beat

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "Stored artifact paths are not portable across hosts"
        labels = @("bug", "P2 - Medium", "component: prediction")
        body = @"
## Description
``ModelVersion.artifact_path`` and LSTM ``summary.json`` record **absolute** paths from whatever host trained them. The registry currently holds paths from a Docker container (``/app/...``), a Linux home directory, and a Windows OneDrive checkout -- none of which resolve on the current host.

A model that loads by version but cannot find its file fails at **inference** time, not at promotion time, which is the worst place to discover it.

## Proposed Fix
Store repo-relative paths and resolve against ``BASE_DIR`` at load time, or add a re-registration step for moved clones.

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "TECHNICAL_GUIDE.md disagrees with the staleness code on relative_volume gap values"
        labels = @("documentation", "P3 - Low", "component: analytics")
        body = @"
## Description
The guide states that ``relative_volume_5d`` and ``relative_volume_20d`` inherit the 5-day and 20-day moving-average gap rules (2 and 5 trading days). ``apps/analytics/technical_staleness.py`` sets both to **0** in ``BASE_MAX_GAP_TRADING_DAYS``.

The code is authoritative; the guide needs correcting.

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "Empty tables behind live features (alerts, screener)"
        labels = @("enhancement", "P2 - Medium", "component: analytics")
        body = @"
## Description
``analytics_alertrule``, ``analytics_alertevent``, ``analytics_screenertemplate``, and ``macro_eventimpactstat`` all have **0 rows**.

- ``check_alert_rules`` runs every 5 minutes against no rules, so the WebSocket alert stream connects and stays permanently silent.
- The Alert Center page combines a live socket with ``/alert-events/`` history and has nothing to show.
- Screener templates are a documented API surface with no templates.

## Action Items
- [ ] Either seed reference data, or document these as user-provisioned and empty by design
- [ ] Right now the features look broken rather than unconfigured

## Source
BACKLOG.md -- Open > correctness
"@
    },
    @{
        title = "Frontend does not type-check or lint clean (6 TS errors + 1 ESLint error)"
        labels = @("bug", "P1 - High", "component: frontend")
        body = @"
## Description
``npm run build`` exits 2 with six TypeScript errors; ``npm run lint`` exits 1 with one error and nine warnings. ``npm test`` passes all 31 -- because vitest transpiles without type-checking, so a green test run says nothing about whether the project compiles.

This is the same blind-spot class as ``manage.py test`` once discovering zero tests and exiting 0.

## Itemised Errors

| Where | Error | Assessment |
|---|---|---|
| ``BacktestWorkbenchPage.test.tsx`` 350, 351, 353 | TS2783 -- duplicate keys | Delete the shadowed ones |
| ``BacktestWorkbenchPage.tsx`` 192, 193 | TS2339 -- ``processed_trading_days`` on ``{}`` | Widen the DTO |
| ``BacktestWorkbenchPage.tsx`` 432 | TS2741 -- ``entry_weekdays`` missing | **The code is right and the type is wrong** -- make the field optional |
| ``629:17`` | ESLint ``react-refresh/only-export-components`` | Move shared constant to its own module |
| 9 warnings | ``react-hooks/exhaustive-deps`` -- missing ``t`` | Add translation function to dependency arrays |

## Follow-up
Fix these, then delete ``continue-on-error`` from the ``frontend-static`` CI job and fold it into the blocking ``frontend`` job.

## Source
BACKLOG.md -- Open > frontend
"@
    },
    @{
        title = "BACKTEST_STALE_TASK_MAX_AGE_SECONDS is not configurable via env"
        labels = @("enhancement", "P3 - Low", "component: backtest")
        body = @"
## Description
``apps/backtest/task_health.py`` reads it via ``getattr(settings, ..., 2400)``, but the setting is never defined and never read from the environment. Changing the stale-run threshold currently requires a code edit.

## Proposed Fix
Wire it through ``env.int()`` in ``config/settings/base.py``.

## Source
BACKLOG.md -- Open > configuration and infrastructure
"@
    },
    @{
        title = "OpenAPI schema metadata is stale"
        labels = @("documentation", "P3 - Low", "component: api-auth")
        body = @"
## Description
- ``SPECTACULAR_SETTINGS['VERSION'] = '1.2.0'`` -- a third version scheme alongside the project version and the README footer, cross-referenced by nothing.
- The schema description says "Chinese Markets **(CSI 300)**", omitting CSI A500.
- The published rate-limit table omits the ``auth`` and legacy ``user`` scopes.

## Source
BACKLOG.md -- Open > configuration and infrastructure
"@
    },
    @{
        title = "Unpinned ML dependencies can produce different models on rebuild"
        labels = @("enhancement", "P1 - High", "component: docker-infra")
        body = @"
## Description
``lightgbm``, ``torch``, ``scikit-learn``, ``mlflow``, ``numpy``, ``akshare``, ``tushare``, ``TA-Lib``, and ``django-celery-beat`` are all unpinned in ``requirements/base.txt``, while the guide documents exact hyperparameters and accuracies. A rebuild on a different day can produce a different model.

## Action Items
- [ ] Pin at least the ML stack in ``requirements/base.txt``
- [ ] Record the resolved set used for each artifact family

Also: ``torch`` installs as a **CPU-only** build from the default index on Windows, which conflicts with the ``windows_gpu`` LightGBM inference backend. Document which runtimes actually have GPU.

## Source
BACKLOG.md -- Open > configuration and infrastructure
"@
    },
    @{
        title = "No test coverage measurement configured"
        labels = @("enhancement", "P3 - Low", "component: celery-tasks")
        body = @"
## Description
No ``coverage`` dependency, no ``.coveragerc``. ``.gitignore`` anticipates ``.coverage`` and ``htmlcov/`` but nothing generates them.

## Proposed Fix
- [ ] Add ``coverage`` to ``requirements/local.txt``
- [ ] Create ``.coveragerc`` with sensible defaults
- [ ] Optionally integrate with CI to report coverage

## Source
BACKLOG.md -- Open > configuration and infrastructure
"@
    },
    @{
        title = "Documentation gaps: missing docstrings, unreferenced scripts, stale CHANGELOG"
        labels = @("documentation", "P2 - Medium")
        body = @"
## Description
Multiple documentation gaps identified:

- **Missing docstrings**: ``apps/backtest/`` has 19 non-test files (~4,100 lines, zero docstrings), including the 2,061-line engine in ``tasks.py``. ``apps/sentiment/`` has 13 files with ~1 docstring.
- **Duplicate import**: ``apps/backtest/management/commands/run_core_backtest_matrix.py`` lines 28-29
- **Unclarified commands**: Relationship between ``onboard_csi_a500_universe`` and ``rollout_csi_a500_universe`` is undocumented
- **Unreferenced scripts**: ``run_local_stack.sh``, ``smoke_api_check.sh``, ``run_staged_news_backfill.sh`` are referenced by no document
- **Stale CHANGELOG**: 0.1.13 is an audit scratch pad; 0.1.12 states env loading moved to ``.envs/.local`` which was reversed
- **LICENSE**: ``README.md`` declares the project private and proprietary but the choice should be conscious

## Source
BACKLOG.md -- Open > documentation
"@
    },
    @{
        title = "requirements/production.txt cannot support the documented deployment"
        labels = @("enhancement", "P3 - Low", "component: docker-infra")
        body = @"
## Description
``requirements/production.txt`` is a single ``-r base.txt`` line. No ``gunicorn``, ``uvicorn``, or ``daphne`` anywhere -- and the WebSocket surface requires an ASGI server. The README's production section describes Kubernetes, RDS, CDN, CI/CD, Prometheus, Sentry, and ELK, none of which exists.

This is a plan, not a working configuration.

## Source
BACKLOG.md -- Open > configuration and infrastructure
"@
    }
)

Write-Host "Creating $($issues.Count) GitHub issues..." -ForegroundColor Cyan
Write-Host "Repository: sean7084/FinanceAnalysis`n" -ForegroundColor Yellow

$created = 0
$errors = 0

foreach ($issue in $issues) {
    try {
        $body = @{
            title  = $issue.title
            labels = $issue.labels
            body   = $issue.body
        } | ConvertTo-Json

        $result = Invoke-RestMethod `
            -Uri "$baseUrl/issues" `
            -Method Post `
            -Headers $headers `
            -Body $body `
            -ContentType "application/json; charset=utf-8"

        Write-Host "[OK] #$($result.number) $($issue.title)" -ForegroundColor Green
        $created++
    } catch {
        Write-Host "[!!] Failed: $($issue.title)" -ForegroundColor Red
        Write-Host "     $($_.Exception.Message)" -ForegroundColor Gray
        $errors++
    }
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Issues created: $created / $($issues.Count)" -ForegroundColor Green
if ($errors -gt 0) {
    Write-Host "Errors:         $errors" -ForegroundColor Red
}
