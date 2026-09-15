# Final Configuration Verification Script for FinanceAnalysis
# Checks labels, branch protection, and prints a summary report.

$repoOwner = "sean7084"
$repoName = "FinanceAnalysis"
$token = (Get-Content '.env' | Select-String '^GITHUB_PERSONAL_ACCESS_TOKEN=').ToString().Split('=', 2)[1].Trim()
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github.v3+json"
}
$baseUrl = "https://api.github.com/repos/$repoOwner/$repoName"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "GitHub Configuration - Status Report" -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow
Write-Host "Date: $(Get-Date -Format 'yyyy-MM-dd')" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Verify Labels ----
Write-Host "[1/3] Checking Labels Configuration..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    $labels = Invoke-RestMethod -Uri "$baseUrl/labels?per_page=100" -Headers $headers -Method Get
    Write-Host "Total Labels Found: $($labels.Count)" -ForegroundColor Green

    # Spot-check critical labels
    $criticalLabels = @(
        "P0 - Critical",
        "bug",
        "enhancement",
        "component: markets",
        "component: frontend",
        "component: prediction",
        "documentation",
        "migration-required",
        "security"
    )
    $foundCritical = 0

    foreach ($labelName in $criticalLabels) {
        if ($labels | Where-Object { $_.name -eq $labelName }) {
            Write-Host "  [OK] [$labelName]" -ForegroundColor Green
            $foundCritical++
        } else {
            Write-Host "  [!!] [$labelName] MISSING" -ForegroundColor Yellow
        }
    }

    Write-Host "`nLabel Coverage: $foundCritical/$($criticalLabels.Count) critical labels found" -ForegroundColor Gray
} catch {
    Write-Host "Could not verify labels: $($_.Exception.Message)" -ForegroundColor Red
}

# ---- 2. Verify Branch Protection ----
Write-Host "`n[2/3] Checking Branch Protection Rules..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    $repoInfo = Invoke-RestMethod -Uri "$baseUrl" -Method Get -Headers $headers
    $branchName = $repoInfo.default_branch

    $protection = Invoke-RestMethod `
        -Uri "$baseUrl/branches/$branchName/protection" `
        -Method Get `
        -Headers $headers

    Write-Host "[OK] Branch Protection Active on: '$branchName'" -ForegroundColor Green
    Write-Host ""
    Write-Host "Configuration:" -ForegroundColor Cyan
    Write-Host "  Required Status Checks:" -ForegroundColor Gray
    $contexts = $protection.required_status_checks.contexts
    if ($contexts -and $contexts.Count -gt 0) {
        foreach ($ctx in $contexts) {
            Write-Host "    - $ctx" -ForegroundColor Gray
        }
    } else {
        Write-Host "    (none configured)" -ForegroundColor Yellow
    }
    Write-Host "  Strict mode: $($protection.required_status_checks.strict)" -ForegroundColor Gray

    Write-Host "  Pull Request Reviews:" -ForegroundColor Gray
    Write-Host "    Required approvals: $($protection.required_pull_request_reviews.required_approving_review_count)" -ForegroundColor Gray
    Write-Host "    Dismiss stale reviews: $($protection.required_pull_request_reviews.dismiss_stale_reviews)" -ForegroundColor Gray

    Write-Host "  Enforce Admins: $($protection.enforce_admins.enabled)" -ForegroundColor Gray
    Write-Host "  Force Pushes Blocked: $(-not $protection.allow_force_pushes.enabled)" -ForegroundColor Gray
    Write-Host "  Deletion Blocked: $(-not $protection.allow_deletions.enabled)" -ForegroundColor Gray
} catch {
    Write-Host "[!!] Branch protection not configured or inaccessible." -ForegroundColor Yellow
    Write-Host "     Error: $($_.Exception.Message)" -ForegroundColor Gray
    Write-Host "     Run scripts/setup-github-branch-protection.ps1 to configure." -ForegroundColor Gray
}

# ---- 3. Verify Issue Templates ----
Write-Host "`n[3/3] Checking Repository Contents..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

$expectedFiles = @(
    ".github/ISSUE_TEMPLATE/bug_report.md",
    ".github/ISSUE_TEMPLATE/feature_request.md",
    ".github/ISSUE_TEMPLATE/task.md",
    ".github/ISSUE_TEMPLATE/security.md",
    ".github/ISSUE_TEMPLATE/data_pipeline.md",
    ".github/ISSUE_TEMPLATE/model_regression.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/LABELS.md",
    ".github/PROJECT_WORKFLOW.md",
    ".github/QUICK_SETUP_AUTOMATION.md"
)

$localRoot = Split-Path -Parent $PSScriptRoot
$foundFiles = 0
foreach ($file in $expectedFiles) {
    $fullPath = Join-Path $localRoot $file
    if (Test-Path $fullPath) {
        Write-Host "  [OK] $file" -ForegroundColor Green
        $foundFiles++
    } else {
        Write-Host "  [!!] $file MISSING" -ForegroundColor Yellow
    }
}
Write-Host "`nLocal Files: $foundFiles/$($expectedFiles.Count) expected files present" -ForegroundColor Gray

# ---- Final Summary ----
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Configuration Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$labelSummary   = if ($labels) { "$($labels.Count) labels on GitHub" } else { "Check manually" }
$templateSummary = "$foundFiles/$($expectedFiles.Count) local files"
$prSummary       = if (Test-Path (Join-Path $localRoot ".github/PULL_REQUEST_TEMPLATE.md")) { "Present" } else { "Missing" }
$protectSummary  = try { if ($protection) { "Active" } else { "Not configured" } } catch { "Not configured" }

$summaryTable = [ordered]@{
    "Labels"              = $labelSummary
    "Issue Templates"     = $templateSummary
    "PR Template"         = $prSummary
    "Branch Protection"   = $protectSummary
    "Project Board"       = "See PROJECT_WORKFLOW.md for setup"
}
$summaryTable | Format-Table | Out-String | Write-Host

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Commit and push .github/ files to the repository" -ForegroundColor Gray
Write-Host "  2. Create/configure project board per .github/PROJECT_WORKFLOW.md" -ForegroundColor Gray
Write-Host "  3. Test by creating a PR to verify branch protection triggers" -ForegroundColor Gray
Write-Host ""
Write-Host "Documentation:" -ForegroundColor Cyan
Write-Host "  .github/LABELS.md              - Label definitions" -ForegroundColor Gray
Write-Host "  .github/PROJECT_WORKFLOW.md     - Project board guide" -ForegroundColor Gray
Write-Host "  .github/QUICK_SETUP_AUTOMATION.md - Automation quick-start" -ForegroundColor Gray
Write-Host ""
