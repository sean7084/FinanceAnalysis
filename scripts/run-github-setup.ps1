# Master GitHub Setup Script for FinanceAnalysis
# Runs all configuration steps in sequence.

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "GitHub Features Configuration - Master Runner" -ForegroundColor Cyan
Write-Host "Repository: sean7084/FinanceAnalysis" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = $PSScriptRoot
$startTime = Get-Date

# Validate token availability
$tokenLine = Get-Content '.env' | Select-String '^GITHUB_PERSONAL_ACCESS_TOKEN='
if (-not $tokenLine) {
    Write-Host "[!!] GITHUB_PERSONAL_ACCESS_TOKEN not found in .env" -ForegroundColor Red
    Write-Host "     Add it to .env and retry." -ForegroundColor Yellow
    exit 1
}
$tokenValue = $tokenLine.ToString().Split('=', 2)[1].Trim()
if ([string]::IsNullOrWhiteSpace($tokenValue)) {
    Write-Host "[!!] GITHUB_PERSONAL_ACCESS_TOKEN is empty in .env" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Token found in .env" -ForegroundColor Green
Write-Host ""

# Step 1: Setup Labels
Write-Host "[STEP 1/3] Setting up GitHub Labels..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    & "$scriptDir\setup-github-labels.ps1"
    Write-Host "`n[OK] STEP 1 COMPLETE - Labels configured`n" -ForegroundColor Green
} catch {
    Write-Host "`n[!!] STEP 1 FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Continuing with remaining steps...`n" -ForegroundColor Yellow
}

# Step 2: Setup Branch Protection
Write-Host "[STEP 2/3] Setting up Branch Protection..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    & "$scriptDir\setup-github-branch-protection.ps1"
    Write-Host "`n[OK] STEP 2 COMPLETE - Branch protection configured`n" -ForegroundColor Green
} catch {
    Write-Host "`n[!!] STEP 2 FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Continuing with remaining steps...`n" -ForegroundColor Yellow
}

# Step 3: Setup Project Board
Write-Host "[STEP 3/3] Setting up Project Board..." -ForegroundColor Cyan
Write-Host "----------------------------------------" -ForegroundColor Gray

try {
    & "$scriptDir\setup-github-project.ps1"
    Write-Host "`n[OK] STEP 3 COMPLETE - Project board initialized`n" -ForegroundColor Green
} catch {
    Write-Host "`n[!!] STEP 3 INCOMPLETE: Manual action may be required" -ForegroundColor Yellow
    Write-Host "See .github/PROJECT_WORKFLOW.md for manual setup instructions`n" -ForegroundColor Gray
}

# Final Summary
$endTime = Get-Date
$totalTime = $endTime - $startTime

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Total Time: $($totalTime.TotalSeconds.ToString('0.0')) seconds" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Actions:" -ForegroundColor Yellow
Write-Host "  1. Commit and push .github/ files to the repository" -ForegroundColor Gray
Write-Host "  2. Verify labels: https://github.com/sean7084/FinanceAnalysis/labels" -ForegroundColor Gray
Write-Host "  3. Configure project board columns and custom fields" -ForegroundColor Gray
Write-Host "  4. Test branch protection by creating a test PR" -ForegroundColor Gray
Write-Host "  5. Run verify script: .\scripts\verify-github-setup.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "Documentation:" -ForegroundColor Cyan
Write-Host "  .github/LABELS.md              - Label definitions" -ForegroundColor Gray
Write-Host "  .github/PROJECT_WORKFLOW.md     - Project board guide" -ForegroundColor Gray
Write-Host "  .github/QUICK_SETUP_AUTOMATION.md - Automation quick-start" -ForegroundColor Gray
Write-Host ""
