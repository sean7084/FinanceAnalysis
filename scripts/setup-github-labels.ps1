# Automated GitHub Label Setup for FinanceAnalysis
# This script creates all ~36 labels with proper colors and descriptions

$repoOwner = "sean7084"
$repoName = "FinanceAnalysis"
$token = (Get-Content '.env' | Select-String '^GITHUB_PERSONAL_ACCESS_TOKEN=').ToString().Split('=', 2)[1].Trim()
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github.v3+json"
}
$baseUrl = "https://api.github.com/repos/$repoOwner/$repoName"

# Define all labels to create
$labels = @(
    # Status (4)
    @{name="triage"; color="E9D75F"; description="Needs initial review and categorization"}
    @{name="awaiting response"; color="0E8A16"; description="Waiting on author feedback"}
    @{name="blocked"; color="BDBDBD"; description="Cannot proceed due to dependency"}
    @{name="stale"; color="EEEEEE"; description="No activity for 30 days"}

    # Priority (4)
    @{name="P0 - Critical"; color="B60205"; description="Blocks release; needs immediate fix"}
    @{name="P1 - High"; color="D93F0B"; description="Important bug/feature for next sprint"}
    @{name="P2 - Medium"; color="FBCA04"; description="Regular backlog item"}
    @{name="P3 - Low"; color="EDEDED"; description="Nice-to-have, low priority"}

    # Type (7)
    @{name="bug"; color="D73A4A"; description="Something isn't working correctly"}
    @{name="enhancement"; color="A2EEEF"; description="New feature request"}
    @{name="documentation"; color="0075CA"; description="Improvements to docs"}
    @{name="good first issue"; color="7057FF"; description="Easy for newcomers"}
    @{name="help wanted"; color="008672"; description="Contribution needed"}
    @{name="question"; color="CC3147"; description="More information needed"}
    @{name="discussion"; color="CCE329"; description="Topics requiring community input"}

    # Component (12)
    @{name="component: markets"; color="1D76EB"; description="Stock data, prices, technical indicators"}
    @{name="component: analytics"; color="0052CC"; description="WebSocket alerts, real-time analytics"}
    @{name="component: prediction"; color="5457FF"; description="LightGBM/LSTM model training and inference"}
    @{name="component: backtest"; color="FBE75F"; description="Backtesting engine and comparison"}
    @{name="component: sentiment"; color="FBF29D"; description="News sentiment analysis"}
    @{name="component: macro"; color="5791EF"; description="Macro-economic data (yield curves, FX)"}
    @{name="component: factors"; color="A2EEEF"; description="Fundamental factor materialization"}
    @{name="component: frontend"; color="79CB23"; description="React/Vite dashboard and UI"}
    @{name="component: ml-pipeline"; color="6F42C1"; description="Feature engineering, label generation"}
    @{name="component: celery-tasks"; color="D8744E"; description="Celery workers, queues, Beat schedule"}
    @{name="component: docker-infra"; color="C6CFCF"; description="Docker Compose, container config"}
    @{name="component: api-auth"; color="EB6420"; description="DRF endpoints, JWT, API-key auth"}

    # Impact (3)
    @{name="breaking change"; color="B60205"; description="Incompatible API/model changes"}
    @{name="deprecation"; color="D8744E"; description="Deprecated feature scheduled for removal"}
    @{name="migration-required"; color="BF5D73"; description="Database migration required"}

    # Testing (3)
    @{name="needs testing"; color="FFEBD6"; description="Awaiting validation"}
    @{name="test-passed"; color="84b6ef"; description="Verified in staging"}
    @{name="e2e-test-needed"; color="7057FF"; description="End-to-end test coverage gap"}

    # Deployment/Release (3)
    @{name="deployment"; color="C2E0C6"; description="Ready for deployment"}
    @{name="release-notes"; color="0366D6"; description="Must be included in changelog"}
    @{name="security"; color="EB6420"; description="Security-related fix"}
)

Write-Host "Starting label creation process..." -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow
Write-Host "Total labels to create: $($labels.Count)`n"

$createdCount = 0
$skippedCount = 0
$errorCount = 0

foreach ($label in $labels) {
    try {
        $body = @{
            name        = $label.name
            color       = $label.color
            description = $label.description
        } | ConvertTo-Json

        $response = Invoke-RestMethod `
            -Uri "$baseUrl/labels" `
            -Method Post `
            -Headers $headers `
            -Body $body `
            -ContentType "application/json"

        Write-Host "[OK] Created: $($label.name)" -ForegroundColor Green
        $createdCount++
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 422) {
            Write-Host "[--] Already exists: $($label.name)" -ForegroundColor DarkGray
            $skippedCount++
        } else {
            Write-Host "[!!] Failed to create: $($label.name)" -ForegroundColor Red
            Write-Host "     Error: $($_.Exception.Message)" -ForegroundColor Gray
            $errorCount++
        }
    }
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Labels created:  $createdCount / $($labels.Count)" -ForegroundColor Green
Write-Host "Labels skipped:  $skippedCount (already existed)" -ForegroundColor DarkGray
if ($errorCount -gt 0) {
    Write-Host "Errors:          $errorCount" -ForegroundColor Red
} else {
    Write-Host "All labels configured successfully!" -ForegroundColor Green
}
